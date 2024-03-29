#!/usr/bin/env python3
'''Search filesystem cruft on a gentoo system, dedicated to all OCD afflicted...

Inspired by ecatmur's cruft script:
http://forums.gentoo.org/viewtopic-t-152618-postdays-0-postorder-asc-start-0.html

- Ignore syntax
    ^/path/single_file$
    ^/path/single_dir/$
    ^/path/subtree$

- pattern/portage data is cached, system tree is always scanned.
    restrict system tree with -p option for faster debugging

====================================================================
FIXME
- concurrent execution of collect_* functions?
- how to exclude symlink without ignoring complete subtree? (eg, /usr/lib, /usr/local/lib)
- provide git-based ebuild in gentoo-overlay (dependencies: gentoolkit, pylon, python3)
- seperate pylon in own repo & provide git-based ebuild
- document how ignore patterns can exclude non-portage files AND
    portage files (eg, mask unavoidable md5 check fails due to eselect)
    option to list excluded portage files?
- create a usecase for a pattern file with ">=asdf-version" in its name
- gentoo forum post
        
====================================================================
'''
import functools
import hashlib
import gentoolkit.equery.check
import os
import pickle
import portage
import pprint
import pylon.base
import pylon.gentoo.job
import pylon.gentoo.ui
import re
import sys
import time

# FIXME configurability (use TOML? https://docs.python.org/3/library/tomllib.html#module-tomllib)
cache_base_path = '/tmp'
cache_base_name = 'cruft_cache'
comment_char = '#'
default_pattern_root = '/usr/bin/cruft.d'

gtk_check = gentoolkit.equery.check.VerifyContents()
trees = portage.create_trees()
vardb = trees[portage.settings['EROOT']]["vartree"].dbapi
vardb_path = os.path.join(portage.settings['EROOT'], portage.const.VDB_PATH)

# portage vartree dict indices
po_type = 0
po_timestamp = 1
po_digest = 2

# cruft dict indices
co_date = 0

class ui(pylon.gentoo.ui.ui):
    def __init__(self, owner):
        super().__init__(owner)
        self.parser_common.add_argument('-i', '--pattern_root',
                                        default=default_pattern_root,
                                        help='give alternative path to directory containing ignore pattern files')
        self.init_op_parser()
        self.parser_report.add_argument('-c', '--check', action='store_true',
                                        help='perform gentoolkit sanity checks on all installed packages (time consuming!)')
        self.parser_report.add_argument('-p', '--path',
                                        default='/',
                                        help='check only specific path for cruft')
        self.parser_report.add_argument('-f', '--format', choices=('path', 'date', 'rm_chain'),
                                        default='path',
                                        help='date: report cruft objects sorted by modification date,\
                                        path: report cruft objects sorted by object path (default),\
                                        rm_chain: report cruft objects as chained rm commands')
        
    def setup(self):
        super().setup()
        if not self.args.op:
            self.parser.print_help()
            raise self.owner.exc_class('Specify at least one subcommand operation')
        
class cruft(pylon.base.base):
    __doc__ = sys.modules[__name__].__doc__
    
    def run_core(self):
        # FIXME use self._data ?
        self.data = dict()
        getattr(self, self.__class__.__name__ + '_' + self.ui.args.op)()

    @functools.lru_cache(typed=True)
    def ignored(self, path):
        return self.data['patterns']['single_regex'].match(path)
        
    @functools.lru_cache(typed=True)
    def collect_ignore_patterns(self):
        self.ui.info('Collecting ignore patterns...')

        pattern_files = list()
        for root, dirs, files in os.walk(self.ui.args.pattern_root):
            for f in files:
                # assume leaf dirs contain package-specific patterns
                if not dirs:
                    # check if any version of the package is installed
                    pkg = os.path.join(os.path.basename(root), f)
                    # working examples:
                    # vardb.match('net-p2p/go-ethereum')
                    # vardb.match('net-p2p/go-ethereum-1.5.5')
                    # vardb.match('net-p2p/go-ethereum[opencl]')
                    if not vardb.match(pkg):
                        self.ui.ext_info('Not installed: ' + pkg)
                        continue
                pattern_files.append(os.path.join(root, f))

        re_map = dict()

        for pattern_file in pattern_files:
            self.ui.ext_info('Extracting patterns from: ' + pattern_file)
            
            # either we generate regexes from executable scripts, ...
            re_list_raw = list()
            if os.access(pattern_file, os.X_OK):
                try:
                    re_list_raw = self.dispatch(pattern_file,
                                                output=None).stdout
                except self.exc_class:
                    self.ui.error('Script failed: ' + pattern_file)

            # ... or we simply read in lines from a text file
            else:
                with open(pattern_file, 'r') as f:
                    for line in f:
                        # ignore comment lines
                        comment_idx = line.find(comment_char)
                        line_no_comments = line
                        if comment_idx != -1:
                            line_no_comments = line[:comment_idx]
                        re_list_raw.append(line_no_comments)

            # - strip all metachars
            # - interpret spaces as delimiter for multiple patterns
            #   on one line. needed for automatic bash expansion by
            #   {}. however this breaks ignore patterns with spaces!
            re_list_of_file = pylon.flatten(x.rstrip(os.linesep).strip().split() for x in re_list_raw)

            # pattern sanity checks, to facilitate pattern file debugging
            for regex in re_list_of_file:
                try:
                    re.compile(regex)
                except Exception:
                    self.ui.error(f'Skipped invalid expression in {pattern_file} ({regex})')
                else:
                    # even if patterns are listed redundantly in one file, just add it once
                    re_map.setdefault(regex, set()).add(pattern_file)

        self.ui.debug('Compiling all expressions into one long regex...')
        re_single_regex = re.compile('|'.join(re_map.keys()))

        return {'map': re_map,
                'single_regex': re_single_regex}
        
    @functools.lru_cache(typed=True)
    def collect_portage_objects(self):
        if 'patterns' not in self.data: self.data['patterns'] = self.collect_ignore_patterns()

        self.ui.info('Collecting objects managed by portage...')
        objects = set()
        for pkg in sorted(vardb.cpv_all()):
            contents = vardb._dblink(pkg).getcontents()

            check = dict()
            for k,v in contents.items():

                # just flatten out the dirname part to avoid tinkering with symlinks introduced by portage itself.
                k = os.path.join(os.path.realpath(os.path.dirname(k)),
                                 os.path.basename(k))
                
                # add trailing slashes to directories for easier regex matching
                if v[po_type] == 'dir':
                    k += '/'
                    
                objects.add(k)
                check[k] = v

            # implicitly checks for missing portage objects
            if self.ui.args.check:
                (n_passed, n_checked, errs) = gtk_check._run_checks(check)
                for err in errs:
                    path = err.split()[0]
                    if not self.ignored(path) and path.startswith(self.ui.args.path):
                        self.ui.error(pkg + ': ' + err)
                
        return objects

    @functools.lru_cache(typed=True)
    def collect_system_objects(self):
        if 'patterns' not in self.data: self.data['patterns'] = self.collect_ignore_patterns()

        self.ui.info('Collecting objects in system tree...')
        objects = set()
        for root, dirs, files in os.walk(self.ui.args.path, followlinks=False, onerror=lambda x: self.ui.error(str(x))):

            for d in list(dirs):
                path = os.path.join(root, d)

                # handle ignored directory symlinks as files
                if os.path.islink(path):
                    dirs.remove(d)
                    files.append(d)
                    continue

                # remove excluded subtrees early to speed up walk (eg, user data)
                # leave dir without slash in objects => filtered by this regex anyway
                if self.ignored(path):
                    dirs.remove(d)
                    objects.add(path)
                    continue

                # add a trailing slash to allow easy distinction between subtree and single dir exclusion
                objects.add(path + '/')
            
            for f in files:
                path = os.path.join(root, f)
                objects.add(path)

                # report broken symlinks but keep them in list (needed for portage - system report)
                if not os.path.exists(path):
                    self.ui.error('Broken symlink detected: ' + path)

        return objects

    def collect_cruft_objects(self):
        if 'patterns' not in self.data: self.data['patterns'] = self.collect_ignore_patterns()
        if 'portage' not in self.data: self.data['portage'] = self.collect_portage_objects()
        if 'system' not in self.data: self.data['system'] = self.collect_system_objects()

        self.ui.info('Identifying cruft...')
        self.ui.debug('Generating difference set (system - portage)...')
        cruft = self.data['system'] - self.data['portage']

        self.ui.debug('Applying ignore patterns on (system - portage)...')
        remaining = {path for path in cruft if not self.ignored(path)}

        self.ui.debug('Removing parent directories of already ignored paths...')
        ignored = cruft - remaining
        for path in ignored:
            remaining = [x for x in remaining if not path.startswith(x) or x[-1] != '/']

        # FIXME use self._n_ignored ?
        self.n_ignored = len(cruft) - len(remaining)

        # add a date info to the remaining objects
        cruft = dict()
        remaining.sort()
        for path in remaining:
            try:
                cruft[path] = [time.localtime(os.lstat(path).st_mtime)]
            except OSError:
                self.ui.error('Path disappeared: ' + path)

        return cruft

    def collect_cached_data(self):
        self.ui.debug('Collecting data and using cache when possible...')
        
        cache_path = os.path.join(cache_base_path, cache_base_name + '_' + self.ui.hostname)
        dirty = False
        if os.access(cache_path, os.R_OK):
            with open(cache_path, 'rb') as cache_file:
                self.ui.info(f'Loading cache {cache_path}...')
                self.data = pickle.load(cache_file)

        # determine portage dir state
        portage_state = hashlib.md5(str(os.stat(vardb_path)).encode('utf-8')).hexdigest()

        # determine pattern dir state
        patterns_state = ''
        for root, dirs, files in os.walk(self.ui.args.pattern_root):
            for f in files:
                patterns_state += hashlib.md5(str(os.stat(os.path.join(root, f))).encode('utf-8')).hexdigest()
        patterns_state = hashlib.md5(patterns_state.encode('utf-8')).hexdigest()
        
        if ('portage' not in self.data or
            'portage_state' not in self.data or
            self.data['portage_state'] != portage_state or
            self.ui.args.check):
          
            # portage changes can affect patterns (deriving patterns from portage API calls),
            # thus collect portage first, which implicitely collects patterns.
            self.data.pop('patterns', None)
            self.data.pop('patterns_state', None)
            self.data['portage'] = self.collect_portage_objects()
            self.data['portage_state'] = portage_state
            dirty = True
        else:
            self.ui.warning('No portage changes detected => reusing cache...')
            
        if ('patterns' not in self.data or
            'patterns_state' not in self.data or
            self.data['patterns_state'] != patterns_state):
          
            self.data['patterns'] = self.collect_ignore_patterns()
            self.data['patterns_state'] = patterns_state
            dirty = True
        else:
            self.ui.warning('No pattern file changes detected => reusing cache...')
           
        if dirty:
            with open(cache_path, 'wb') as cache_file:
                self.ui.info('Storing cache...')
                pickle.dump(self.data, cache_file)

    @pylon.log_exec_time
    def cruft_report(self):
        '''
        identify potential cruft objects on your system
        '''
        self.collect_cached_data()
        # FIXME use self._cruft_dict ?
        self.cruft_dict = self.collect_cruft_objects()

        if self.cruft_dict:
            cruft_keys = list(self.cruft_dict.keys())

            # useful sort keys
            path = lambda x: x
            date = lambda x: self.cruft_dict[x][co_date]
            path_str = lambda x: path(x)
            date_str = lambda x: time.asctime(date(x))

            # sort & format according to option
            fmt = '{path_str}, {date_str}'
            reverse = False
            sort_key = path
            if self.ui.args.format == 'date':
                reverse = True
                sort_key = date
            if self.ui.args.format == 'rm_chain':
                fmt = 'rm -rf "{path_str}" && \\'
            cruft_keys.sort(key=sort_key, reverse=reverse)

            self.ui.info('Cruft objects:' + os.linesep +
                         os.linesep.join(
                             [fmt.format(path_str=path_str(co),
                                         date_str=date_str(co))
                              for co in cruft_keys]))
            self.ui.warning(f'Cruft objects identified: {len(cruft_keys)}')

        self.ui.info(f'Cruft files ignored: {self.n_ignored}')

    @pylon.log_exec_time
    def cruft_list(self):
        '''
        list ignore patterns and their origin + do some sanity checking
        '''

        # FIXME check idea: ignored files which have not been updated in a while => potentially incorrect ignore pattern?
        # FIXME check idea: determine nr of files in excluded subtrees => list largest ones
        # FIXME check idea: list pattern files for packages which are not installed => delete, or keep for larger user base?
        
        # re-using functions from report op requires sane args defaults
        self.ui.args.check = False
        self.ui.args.path = '/'

        self.collect_cached_data()
        if 'system' not in self.data: self.data['system'] = self.collect_system_objects()

        # FIXME put this verbose info into a separate operation
        self.ui.info('List of patterns and the files which generated them:')
        pprint.pprint(self.data['patterns']['map'])
        
        # do some sanity checking
        self.ui.info('Identical patterns are listed in multiple files:')
        pprint.pprint({k:v for k,v in self.data['patterns']['map'].items() if len(v) != 1})

        # FIXME multiprocessing? takes too long, output too verbose
        # FIXME try to match with single_regex first, if match => iterate through every pattern
        self.ui.info('Redundant ignore patterns (remove from pattern file, or leave it to mask MD5 fails):')
        for k,v in sorted(self.data['patterns']['map'].items()):
            matched = False
            pattern = re.compile(k)
            for path in self.data['portage']:
                if pattern.match(path):
                    matched = True
                    break
            if matched:
                pprint.pprint({k:v})
                
        # FIXME multiprocessing? takes too long, output too verbose
        self.ui.info('Non-matching patterns (be patient!):')
        for k,v in sorted(self.data['patterns']['map'].items()):
            matched = False
            pattern = re.compile(k)
            for path in self.data['system']:
                if pattern.match(path):
                    matched = True
                    break
            if not matched:
                pprint.pprint({k:v})
                
if __name__ == '__main__':
    app = cruft(job_class=pylon.gentoo.job.job,
                ui_class=ui)
    app.run()
    #import cProfile
    #try:
    #   cProfile.run('app.run()', '/tmp/fooprof')
    #except:
    #   ...
    #import pstats
    #p = pstats.Stats('/tmp/fooprof')
    #p.sort_stats('cumulative').print_stats(30)
    #p.sort_stats('time').print_stats(30)

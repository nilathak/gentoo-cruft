#!/usr/bin/env -S python3 -Wdefault
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
- pkgcore might provide faster portage db operations, but it's a dependency (pkgcore/pkgdev seem to be new gentoo dev approved tools)
  portage python bindings used in cruft.py and /usr/bin/cruft.d/sys-apps/portage
  from pkgcore.config import load_config
  from pkgcore.repository.util import get_virtual_repos
   
  def get_package_completions_pkgcore(partial_name):
      config = load_config()
      repo = get_virtual_repos(config.objects["repo-stack"].repos)
     
      results = []
      for pkg in repo:
          if pkg.key.startswith(partial_name):
              results.append({
                  "name": pkg.key,
                  "version": str(pkg.version),
                  "description": pkg.description
              })
     
      return results
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

import asyncio
import functools
import hashlib
import io
import os
import pickle
import pylon
import re
import sys
import time
import portage
import gentoolkit.equery.check

# FIXME configurability (use TOML? https://docs.python.org/3/library/tomllib.html#module-tomllib)
cache_base_path = '/tmp'
cache_base_name = 'cruft_cache'
comment_char = '#'
default_pattern_root = '/usr/bin/cruft.d'

gtk_check = gentoolkit.equery.check.VerifyContents()
trees = portage.create_trees()
vardb = trees[portage.settings['EROOT']]["vartree"].dbapi
vardb_path = os.path.join(portage.settings['EROOT'], portage.const.VDB_PATH)

class cruft(pylon.gentoo_cli.gentoo_cli):
    __doc__ = sys.modules[__name__].__doc__
    
    def __init__(self):
        super().__init__()
        self.parser_common.add_argument('-i', '--pattern_root',
                                        default=default_pattern_root,
                                        help='give alternative path to directory containing ignore pattern files')
        self.init_subcommands()
        self.parser_report.add_argument('-c', '--check', action='store_true',
                                        help='perform gentoolkit sanity checks on all installed packages (time consuming!)')
        self.parser_report.add_argument('-p', '--path',
                                        default='/',
                                        help='check only specific path for cruft')
        self.parser_report.add_argument('-f', '--format', choices=('path', 'date', 'rm_chain'),
                                        default='path',
                                        help='date: report cruft objects sorted by modification date, '
                                        'path: report cruft objects sorted by object path (default), '
                                        'rm_chain: report cruft objects as chained rm commands')

    def ignored(self, path):
        'check if a path matches the ignore pattern regex.'
        return self.data['patterns']['single_regex'].match(path)

    async def collect_ignore_patterns(self):
        self.logger.info('Collecting ignore patterns...')
        
        pattern_files = list()
        for root, dirs, files in os.walk(self.args.pattern_root):
            for f in files:
                # assume leaf dirs contain package-specific patterns
                if not dirs:
                    # check if any version of the package is installed
                    pkg = os.path.join(os.path.basename(root), f)
                    if not vardb.match(pkg):
                        self.logger.debug('Not installed: ' + pkg)
                        continue
                    self.logger.debug('Installed: ' + pkg)
                pattern_files.append(os.path.join(root, f))

        re_map = dict()
        
        for pattern_file in pattern_files:
            self.logger.debug('Extracting patterns from: ' + pattern_file)
            
            # either we generate regexes from executable scripts, ...
            re_list_raw = list()
            if os.access(pattern_file, os.X_OK):
                try:
                    out = io.StringIO()
                    await self.dispatch(pattern_file, output=(None, out))
                    # FIXME
                    re_list_raw = out.getvalue().splitlines()
                except pylon.script_error:
                    self.logger.error('Script failed: ' + pattern_file)
                    
            # ... or we simply read in lines from a text file
            else:
                with open(pattern_file, 'r') as f:
                    for line in f:
                        # ignore comment lines
                        comment_idx = line.find(comment_char)
                        line_no_comments = line if comment_idx == -1 else line[:comment_idx]
                        re_list_raw.append(line_no_comments)
                        
            # - strip all metachars
            # - interpret spaces as delimiter for multiple patterns
            #   on one line. needed for automatic bash expansion by
            #   {}. however this breaks ignore patterns with spaces!
            # FIXME
            re_list_of_file = pylon.flatten(x.strip().split() for x in re_list_raw)

            # pattern sanity checks, to facilitate pattern file debugging
            for regex in re_list_of_file:
                try:
                    re.compile(regex)
                except Exception:
                    self.logger.error(f'Skipped invalid expression in {pattern_file} ({regex})')
                else:
                    # even if patterns are listed redundantly in one file, just add it once
                    re_map.setdefault(regex, set()).add(pattern_file)
                    
        self.logger.debug('Compiling all expressions into one long regex...')
        re_single_regex = re.compile('|'.join(re_map.keys()))
        
        return {'map': re_map,
                'single_regex': re_single_regex}

    async def collect_portage_objects(self):
        if 'patterns' not in self.data:
            self.data['patterns'] = await self.collect_ignore_patterns()
            
        self.logger.info('Collecting objects managed by portage...')
        objects = set()
        for pkg in sorted(vardb.cpv_all()):
            contents = vardb._dblink(pkg).getcontents()
            
            check = dict()
            for k, v in contents.items():
                
                # just flatten out the dirname part to avoid tinkering with symlinks introduced by portage itself.
                k = os.path.join(os.path.realpath(os.path.dirname(k)), os.path.basename(k))
                
                # add trailing slashes to directories for easier regex matching
                if v[0] == 'dir':
                    k += '/'
                    
                objects.add(k)
                check[k] = v
                
            # implicitly checks for missing portage objects
            if self.args.check:
                n_passed, n_checked, errs = gtk_check._run_checks(check)
                for err in errs:
                    path = err.split()[0]
                    if not self.ignored(path) and path.startswith(self.args.path):
                        self.logger.error(pkg + ': ' + err)
                        
        return objects

    async def collect_system_objects(self):
        """Collect all objects in the system tree."""
        if 'patterns' not in self.data:
            self.data['patterns'] = await self.collect_ignore_patterns()
            
        self.logger.info('Collecting objects in system tree...')
        objects = set()
        for root, dirs, files in os.walk(self.args.path, followlinks=False, onerror=lambda x: self.logger.error(str(x))):
            
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
                    self.logger.error('Broken symlink detected: ' + path)
                    
        return objects

    async def collect_cruft_objects(self):
        if 'patterns' not in self.data:
            self.data['patterns'] = await self.collect_ignore_patterns()
        if 'portage' not in self.data:
            self.data['portage'] = await self.collect_portage_objects()
        if 'system' not in self.data:
            self.data['system'] = await self.collect_system_objects()
            
        self.logger.info('Identifying cruft...')
        self.logger.debug('Generating difference set (system - portage)...')
        cruft = self.data['system'] - self.data['portage']
        
        self.logger.debug('Applying ignore patterns on (system - portage)...')
        remaining = {path for path in cruft if not self.ignored(path)}
        
        self.logger.debug('Removing parent directories of already ignored paths...')
        ignored = cruft - remaining
        for path in ignored:
            remaining = {x for x in remaining if not path.startswith(x) or x[-1] != '/'}
            
        # FIXME use self._n_ignored ?
        self.n_ignored = len(cruft) - len(remaining)
        
        # add a date info to the remaining objects
        cruft_dict = dict()
        remaining = sorted(remaining)
        for path in remaining:
            try:
                cruft_dict[path] = [time.localtime(os.lstat(path).st_mtime)]
            except OSError:
                self.logger.error('Path disappeared: ' + path)
                
        return cruft_dict

    async def collect_cached_data(self):
        self.logger.debug('Collecting data and using cache when possible...')
        
        cache_path = os.path.join(cache_base_path, cache_base_name + '_' + self.hostname)
        dirty = False
        if os.access(cache_path, os.R_OK):
            with open(cache_path, 'rb') as cache_file:
                self.logger.info(f'Loading cache {cache_path}...')
                self.data = pickle.load(cache_file)
                
        # determine portage dir state
        portage_state = hashlib.md5(str(os.stat(vardb_path)).encode('utf-8')).hexdigest()

        # determine pattern dir state
        patterns_state = ''
        for root, dirs, files in os.walk(self.args.pattern_root):
            for f in files:
                patterns_state += hashlib.md5(str(os.stat(os.path.join(root, f))).encode('utf-8')).hexdigest()
        patterns_state = hashlib.md5(patterns_state.encode('utf-8')).hexdigest()
        
        if ('portage' not in self.data or
            'portage_state' not in self.data or
            self.data['portage_state'] != portage_state or
            self.args.check):
            
            # portage changes can affect patterns (deriving patterns from portage API calls),
            # thus collect portage first, which implicitely collects patterns.
            self.data.pop('patterns', None)
            self.data.pop('patterns_state', None)
            self.data['portage'] = await self.collect_portage_objects()
            self.data['portage_state'] = portage_state
            dirty = True
        else:
            self.logger.warning('No portage changes detected => reusing cache...')
            
        if ('patterns' not in self.data or
            'patterns_state' not in self.data or
            self.data['patterns_state'] != patterns_state):

            # FIXME this is called twice (first portage or system calls patterns implicitly, second no patterns_state when uncached => called again)
            self.data['patterns'] = await self.collect_ignore_patterns()
            self.data['patterns_state'] = patterns_state
            dirty = True
        else:
            self.logger.warning('No pattern file changes detected => reusing cache...')
            
        if dirty:
            with open(cache_path, 'wb') as cache_file:
                self.logger.info('Storing cache...')
                pickle.dump(self.data, cache_file)

    @pylon.gentoo_cli.subcommand
    async def report(self):
        # ====================================================================
        'identify potential cruft objects on your system'
        # FIXME
        self.data = {}  # Initialize data dictionary
        
        await self.collect_cached_data()
        # FIXME use self._cruft_dict ?
        self.cruft_dict = await self.collect_cruft_objects()
        
        if self.cruft_dict:
            cruft_keys = list(self.cruft_dict.keys())
            
            # useful sort keys
            path = lambda x: x
            date = lambda x: self.cruft_dict[x][0]
            path_str = lambda x: path(x)
            date_str = lambda x: time.asctime(date(x))
            
            # sort & format according to option
            fmt = '{path_str}, {date_str}'
            reverse = False
            sort_key = path
            if self.args.format == 'date':
                reverse = True
                sort_key = date
            if self.args.format == 'rm_chain':
                fmt = 'rm -rf "{path_str}" && \\'
            cruft_keys.sort(key=sort_key, reverse=reverse)
            
            self.logger.info('Cruft objects:' + os.linesep +
                             os.linesep.join(fmt.format(path_str=path_str(co),
                                                        date_str=date_str(co))
                                             for co in cruft_keys))
            self.logger.warning(f'Cruft objects identified: {len(cruft_keys)}')
            
        self.logger.info(f'Cruft files ignored: {self.n_ignored}')

    @pylon.gentoo_cli.subcommand
    async def list(self):
        # ====================================================================
        'list ignore patterns and their origin + do some sanity checking'

        # FIXME check idea: ignored files which have not been updated in a while => potentially incorrect ignore pattern?
        # FIXME check idea: determine nr of files in excluded subtrees => list largest ones
        # FIXME check idea: list pattern files for packages which are not installed => delete, or keep for larger user base?

        # FIXME
        self.data = {}  # Initialize data dictionary
        
        # re-using functions from report op requires sane args defaults
        self.args.check = False  # Ensure sane defaults
        self.args.path = '/'
        
        await self.collect_cached_data()
        if 'system' not in self.data:
            self.data['system'] = await self.collect_system_objects()
            
        # FIXME put this verbose info into a separate operation
        self.logger.info('List of patterns and the files which generated them:')
        import pprint
        pprint.pprint(self.data['patterns']['map'])
        
        # do some sanity checking
        self.logger.info('Identical patterns are listed in multiple files:')
        pprint.pprint({k: v for k, v in self.data['patterns']['map'].items() if len(v) != 1})
        
        # FIXME multiprocessing? takes too long, output too verbose
        # FIXME try to match with single_regex first, if match => iterate through every pattern
        #self.ui.info('Redundant ignore patterns (remove from pattern file, or leave it to mask MD5 fails):')
        #for k,v in sorted(self.data['patterns']['map'].items()):
        #    matched = False
        #    pattern = re.compile(k)
        #    for path in self.data['portage']:
        #        if pattern.match(path):
        #            matched = True
        #            break
        #    if matched:
        #        pprint.pprint({k:v})
        #        
        ## FIXME multiprocessing? takes too long, output too verbose
        #self.ui.info('Non-matching patterns (be patient!):')
        #for k,v in sorted(self.data['patterns']['map'].items()):
        #    matched = False
        #    pattern = re.compile(k)
        #    for path in self.data['system']:
        #        if pattern.match(path):
        #            matched = True
        #            break
        #    if not matched:
        #        pprint.pprint({k:v})

if __name__ == '__main__':
    app = cruft()
    asyncio.run(app.run())

    # FIXME
    #import cProfile
    #try:
    #   cProfile.run('app.run()', '/tmp/fooprof')
    #except:
    #   ...
    #import pstats
    #p = pstats.Stats('/tmp/fooprof')
    #p.sort_stats('cumulative').print_stats(30)
    #p.sort_stats('time').print_stats(30)

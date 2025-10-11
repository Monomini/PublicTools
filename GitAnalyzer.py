#!/usr/bin/env python
# -*- encoding: utf-8 -*-

import multiprocessing
import sys
import os
import zlib
import binascii
import re
from datetime import datetime
import collections
import struct
import difflib
import requests
try:
    import argparse
    import dulwich.repo
    import dulwich.objects
    import dulwich.index
    import dulwich.pack
    from dulwich.errors import NotGitRepository
except ImportError:
    print("Error: The 'dulwich' library is required. Please install it using: pip install dulwich", file=sys.stderr)
    sys.exit(1)
try:
    import bs4
except ImportError:
    print("Error: The 'beautifulsoup4' library is required for the download command. Please install it using: pip install beautifulsoup4", file=sys.stderr)
    # We don't exit here, as analyze commands might still work.

class GitAnalyzer:
    def __init__(self, git_dir):
        # Allow either a '.git' directory or our custom 'git_repo' directory.
        dir_name = os.path.basename(os.path.normpath(git_dir))
        if not os.path.isdir(git_dir) or dir_name not in ['.git', 'git_repo']:
            raise ValueError(f"Path '{git_dir}' is not a valid .git or git_repo directory.")
        try:
            self.repo = dulwich.repo.Repo(git_dir)
            self.git_dir = git_dir # Keep for path lookups outside of dulwich
        except NotGitRepository:
            raise ValueError(f"'{git_dir}' is not a valid git repository.")

    def _read_object(self, sha):
        """Reads a git object by its SHA and returns its type and decompressed data."""
        try:
            obj = self.repo.get_object(sha.encode('ascii'))
            if isinstance(obj, dulwich.objects.Blob):
                return 'blob', obj.data
            elif isinstance(obj, dulwich.objects.Tree):
                return 'tree', obj.as_raw_string()
            elif isinstance(obj, dulwich.objects.Commit):
                return 'commit', obj.as_raw_string()
            elif isinstance(obj, dulwich.objects.Tag):
                return 'tag', obj.as_raw_string()
            return 'unknown', obj.as_raw_string()
        except KeyError:
            return None, None

    def _resolve_ref(self, ref):
        """Resolves a branch, tag, or SHA into a commit SHA."""
        if re.match(r'^[0-9a-f]{40}' , ref):
            # Check if it's a commit SHA directly
            obj_type, _ = self._read_object(ref)
            if obj_type == 'commit':
                return ref

        # Let dulwich resolve branches and tags
        refs_to_try = [
            f"refs/heads/{ref}".encode('ascii'),
            f"refs/tags/{ref}".encode('ascii'),
            ref.encode('ascii')
        ]
        for ref_bytes in refs_to_try:
            if ref_bytes in self.repo.refs:
                return self.repo.refs[ref_bytes].decode('ascii')
        raise ValueError(f"Could not resolve reference: {ref}")

    def _get_tree_sha_from_commit(self, commit_sha):
        obj_type, content = self._read_object(commit_sha)
        if obj_type != 'commit':
            return None
        
        content_str = content.decode('utf-8', 'replace')
        tree_match = re.search(r'tree ([0-9a-f]{40})', content_str)
        if not tree_match:
            return None
        return tree_match.group(1)

    def _walk_tree(self, tree_sha, path_prefix=""):
        """Recursively walk a tree and yield blobs with their full path."""
        obj_type, content = self._read_object(tree_sha)
        if obj_type != 'tree':
            return

        pos = 0
        while pos < len(content):
            mode_end = content.find(b' ', pos)
            mode = content[pos:mode_end].decode()
            name_end = content.find(b'\x00', mode_end + 1)
            name = content[mode_end + 1:name_end].decode('utf-8', 'replace')
            sha_start = name_end + 1
            sha = binascii.hexlify(content[sha_start:sha_start + 20]).decode('ascii')
            
            full_path = os.path.join(path_prefix, name)
            
            entry_obj_type, _ = self._read_object(sha)

            if entry_obj_type == 'blob':
                yield 'blob', mode, sha, full_path
            elif entry_obj_type == 'tree':
                yield from self._walk_tree(sha, full_path)

            pos = sha_start + 20

    def info(self):
        """Displays basic repository information."""
        refs = self.repo.get_refs()
        print("--- Branches ---")
        for ref, sha in refs.items():
            if ref.startswith(b'refs/heads/'):
                branch_name = ref.decode().split('/')[-1]
                print(f"  {branch_name:<20} {sha.decode()}")

        print("\n--- Tags ---")
        for ref, sha in refs.items():
            if ref.startswith(b'refs/tags/'):
                tag_name = ref.decode().split('/')[-1]
                print(f"  {tag_name:<20} {sha.decode()}")

    def log(self, branch):
        """Shows the commit history for a given branch."""
        commit_sha = self._resolve_ref(branch)
        processed_commits = set()
        while commit_sha and commit_sha not in processed_commits:
            processed_commits.add(commit_sha)
            obj_type, content = self._read_object(commit_sha)
            if obj_type != 'commit':
                break
            
            content_str = content.decode('utf-8', 'replace')
            author_match = re.search(r'author (.+?) (\d+) ([+-]\d{4})', content_str)
            message = content_str.split('\n\n', 1)[1].strip()

            print(f"commit {commit_sha}")
            if author_match:
                author = author_match.group(1)
                timestamp = int(author_match.group(2))
                date = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
                print(f"Author: {author}")
                print(f"Date:   {date}")
            
            print("\n    " + "\n    ".join(message.split('\n')) + "\n")

            parent_match = re.search(r'parent ([0-9a-f]{40})', content_str)
            commit_sha = parent_match.group(1) if parent_match else None

    def ls_tree(self, ref, recursive=False):
        """Lists the files and directories in a specific commit."""
        commit_sha = self._resolve_ref(ref)
        tree_sha = self._get_tree_sha_from_commit(commit_sha)
        if not tree_sha:
            raise ValueError(f"Could not find a tree for reference {ref}")

        if not recursive:
            obj_type, content = self._read_object(tree_sha)
            if obj_type != 'tree':
                raise ValueError(f"Object {tree_sha} is not a tree.")
            pos = 0
            while pos < len(content):
                mode_end = content.find(b' ', pos)
                mode = content[pos:mode_end].decode()
                name_end = content.find(b'\x00', mode_end + 1)
                name = content[mode_end + 1:name_end].decode('utf-8', 'replace')
                sha_start = name_end + 1
                sha = binascii.hexlify(content[sha_start:sha_start + 20]).decode('ascii')
                obj_type, _ = self._read_object(sha)
                print(f"{mode} {obj_type} {sha}\t{name}")
                pos = sha_start + 20
        else:
            for obj_type, mode, sha, full_path in self._walk_tree(tree_sha):
                print(f"{mode} {obj_type} {sha}\t{full_path}")

    def history(self, list_all=False):
        """Lists all files found in the repository's history."""
        print("[+] Finding all commits...")
        commit_heads = [sha.decode('ascii') for ref, sha in self.repo.get_refs().items() if b'refs/heads/' in ref]
        # Also include loose heads like from FETCH_HEAD
        for head_file in ['HEAD', 'ORIG_HEAD', 'FETCH_HEAD']:
            head_file_bytes = head_file.encode('ascii')
            sha_bytes = self.repo.refs.read_ref(head_file_bytes)
            if sha_bytes:
                commit_heads.append(sha_bytes.decode('ascii'))

        processed_commits = set()
        all_files = set()
        
        commits_to_process = list(set(commit_heads))

        while commits_to_process:
            commit_sha = commits_to_process.pop(0)
            if not commit_sha or commit_sha in processed_commits:
                continue
            
            processed_commits.add(commit_sha)
            obj_type, content = self._read_object(commit_sha)
            if obj_type != 'commit':
                continue
            
            content_str = content.decode('utf-8', 'replace')
            tree_match = re.search(r'tree ([0-9a-f]{40})', content_str)
            if not tree_match:
                continue
            tree_sha = tree_match.group(1)
            
            parents = re.findall(r'parent ([0-9a-f]{40})', content_str)
            commits_to_process.extend(parents)

            if list_all:
                print(f"\n--- Files in commit {commit_sha} ---")

            for _, _, _, full_path in self._walk_tree(tree_sha):
                if list_all:
                    print(full_path)
                else:
                    all_files.add(full_path)

        if not list_all:
            print("\n--- Unique files in history ---")
            for file_path in sorted(list(all_files)):
                print(file_path)

    def cat_file(self, sha):
        """Prints the content of a git object."""
        obj_type, content = self._read_object(sha)
        if not obj_type:
            print(f"Object not found: {sha}", file=sys.stderr)
            return

        print(f"Type: {obj_type}")
        if obj_type == 'blob':
            try:
                print("\n" + content.decode('utf-8'))
            except UnicodeDecodeError:
                print("\n<binary content>")
        else:
            print("\n" + content.decode('utf-8', 'replace'))

    def diff(self, ref1, ref2=None):
        """Shows the changes between two commits."""
        EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904" # SHA for an empty tree

        commit_sha1 = self._resolve_ref(ref1)
        if not ref2:
            obj_type, content = self._read_object(commit_sha1)
            if obj_type != 'commit':
                raise ValueError(f"Object {commit_sha1} is not a commit.")
            content_str = content.decode('utf-8', 'replace')
            parent_match = re.search(r'parent ([0-9a-f]{40})', content_str)
            if not parent_match:
                # This is the initial commit, compare against an empty tree
                ref2 = EMPTY_TREE_SHA
            else:
                ref2 = parent_match.group(1)

        commit_sha2 = self._resolve_ref(ref2)

        tree_sha1 = self._get_tree_sha_from_commit(commit_sha1)
        tree_sha2 = self._get_tree_sha_from_commit(commit_sha2) if ref2 != EMPTY_TREE_SHA else EMPTY_TREE_SHA

        if not tree_sha1:
            raise ValueError(f"Could not find tree for reference {ref1}")

        files1 = {path: sha for _, _, sha, path in self._walk_tree(tree_sha1)}
        files2 = {path: sha for _, _, sha, path in self._walk_tree(tree_sha2)} if tree_sha2 else {}

        added_files = set(files1.keys()) - set(files2.keys())
        deleted_files = set(files2.keys()) - set(files1.keys())
        common_files = set(files1.keys()) & set(files2.keys())

        print(f"--- Diff between {ref2[:7]} and {ref1} ({commit_sha1[:7]}) ---")

        for file in sorted(added_files):
            print(f"[+] Added: {file}")
            _, content = self._read_object(files1[file])
            diff = difflib.unified_diff(
                [],
                content.decode(errors='ignore').splitlines(),
                fromfile=f'a/{file}',
                tofile=f'b/{file}',
                lineterm=''
            )
            for line in diff:
                print(line)

        for file in sorted(deleted_files):
            print(f"[-] Deleted: {file}")

        for file in sorted(common_files):
            if files1[file] != files2[file]:
                print(f"[*] Modified: {file}")
                _, content1 = self._read_object(files2[file])
                _, content2 = self._read_object(files1[file])
                
                diff = difflib.unified_diff(
                    content1.decode(errors='ignore').splitlines(),
                    content2.decode(errors='ignore').splitlines(),
                    fromfile=f'a/{file}', 
                    tofile=f'b/{file}',
                    lineterm=''
                )
                for line in diff:
                    print(line)

    def recover(self, ref, output_dir):
        """Reconstructs all files from a specific commit."""
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        elif not os.path.isdir(output_dir):
            raise ValueError(f"Output path '{output_dir}' exists and is not a directory.")

        commit_sha = self._resolve_ref(ref)
        tree_sha = self._get_tree_sha_from_commit(commit_sha)
        if not tree_sha:
            raise ValueError(f"Could not find a tree for reference {ref}")

        print(f"Starting recovery from commit {commit_sha} into '{output_dir}'...")
        for obj_type, _, sha, full_path in self._walk_tree(tree_sha):
            if obj_type == 'blob':
                _, obj_content = self._read_object(sha)
                final_path = os.path.join(output_dir, full_path)
                final_dir = os.path.dirname(final_path)
                if not os.path.exists(final_dir):
                    os.makedirs(final_dir)
                print(f"  Writing file... {final_path}")
                with open(final_path, 'wb') as f:
                    f.write(obj_content)
        print("Recovery complete.")

# --- Start of git-dumper integration ---

def printf(fmt, *args, file=sys.stdout):
    if args:
        fmt = fmt % args
    file.write(fmt)
    file.flush()

def is_html(response):
    """ Return True if the response is a HTML webpage """
    return (
        "Content-Type" in response.headers
        and "text/html" in response.headers["Content-Type"]
    )

def is_safe_path(path):
    """ Prevent directory traversal attacks """
    # This is a simplified check for relative paths within the git repo context.
    return ".." not in path.split(os.path.sep) and not os.path.isabs(path)

def get_indexed_files(response):
    """ Return all the files in the directory index webpage """
    html = bs4.BeautifulSoup(response.text, "html.parser")
    files = []
    for link in html.find_all("a"):
        href = link.get("href")
        # Basic filtering for relative links, ignoring parent dir links, etc.
        if href and not href.startswith(('?', '/', '..', 'http:', 'https:')):
            files.append(href)
    return files

def verify_response(response):
    if response.status_code != 200:
        return (False, f"[-] %s/%s responded with status code {response.status_code}\n")
    elif "Content-Length" in response.headers and response.headers["Content-Length"] == "0":
        return False, "[-] %s/%s responded with a zero-length body\n"
    elif "Content-Type" in response.headers and "text/html" in response.headers["Content-Type"]:
        return False, "[-] %s/%s responded with HTML\n"
    else:
        return True, True

def create_intermediate_dirs(path):
    dirname, _ = os.path.split(path)
    if dirname and not os.path.exists(dirname):
        try:
            os.makedirs(dirname)
        except FileExistsError:
            pass

def get_referenced_sha1(obj_file):
    objs = []
    if isinstance(obj_file, dulwich.objects.Commit):
        objs.append(obj_file.tree.decode())
        for parent in obj_file.parents:
            objs.append(parent.decode())
    elif isinstance(obj_file, dulwich.objects.Tree):
        for item in obj_file.iteritems():
            objs.append(item.sha.decode())
    return objs

def sanitize_file(filepath):
    """Inplace comment out possibly unsafe lines based on regex."""
    if not os.path.isfile(filepath): return
    UNSAFE = r"dumpping"
    UNSAFE = r"^\s*fsmonitor|sshcommand|askpass|editor|pager"
    with open(filepath, 'r+') as f:
        content = f.read()
        modified_content = re.sub(UNSAFE, r'# \g<0>', content, flags=re.IGNORECASE)
        if content != modified_content:
            printf("Warning: '%s' file was altered\n" % filepath)

            f.seek(0)
            f.write(modified_content)
            f.truncate()

class Worker(multiprocessing.Process):
    def __init__(self, pending_tasks, tasks_done, args):
        super().__init__()
        self.daemon = True
        self.pending_tasks = pending_tasks
        self.tasks_done = tasks_done
        self.args = args

    def run(self):
        self.init(*self.args)
        while True:
            task = self.pending_tasks.get(block=True)
            if task is None: return
            try:
                result = self.do_task(task, *self.args)
            except Exception as e:
                printf(f"Task {task} raised exception: {e}\n", file=sys.stderr)
                result = []
            self.tasks_done.put(result)

    def init(self, *args):
        raise NotImplementedError

    def do_task(self, task, *args):
        raise NotImplementedError

def process_tasks(initial_tasks, worker, jobs, args=(), tasks_done=None):
    if not initial_tasks: return
    tasks_seen = set(tasks_done) if tasks_done else set()
    pending_tasks = multiprocessing.Queue()
    tasks_done_queue = multiprocessing.Queue()
    num_pending_tasks = 0

    for task in initial_tasks:
        if task not in tasks_seen:
            pending_tasks.put(task)
            num_pending_tasks += 1
            tasks_seen.add(task)

    processes = [worker(pending_tasks, tasks_done_queue, args) for _ in range(jobs)]
    for p in processes: p.start()

    while num_pending_tasks > 0:
        task_result = tasks_done_queue.get(block=True)
        num_pending_tasks -= 1
        for task in task_result:
            if task not in tasks_seen:
                pending_tasks.put(task)
                num_pending_tasks += 1
                tasks_seen.add(task)

    for _ in range(jobs): pending_tasks.put(None)
    for p in processes: p.join()

class DownloadWorker(Worker):
    def init(self, url, directory, timeout, headers):
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers = headers

    def do_task(self, filepath, url, directory, timeout, headers):
        # filepath is the remote path (e.g., 'HEAD', 'objects/info/packs')
        # local_filepath is where we save it (e.g., 'git_repo/HEAD')
        local_filepath = os.path.join('git_repo', filepath)
        if os.path.isfile(os.path.join(directory, local_filepath)):
            printf("[-] Already downloaded %s/%s\n", url, filepath)
            return []

        with self.session.get(f"{url}/{filepath}", stream=True, timeout=timeout) as response:
            printf("[-] Fetching %s/%s [%d]\n", url, filepath, response.status_code)
            valid, error_message = verify_response(response)
            if not valid:
                printf(error_message % (url, filepath), file=sys.stderr)
                return []

            abspath = os.path.abspath(os.path.join(directory, local_filepath))
            create_intermediate_dirs(abspath)
            with open(abspath, "wb") as f:
                for chunk in response.iter_content(4096):
                    f.write(chunk)
        return []

class RecursiveDownloadWorker(DownloadWorker):
    """Download a directory recursively."""

    def do_task(self, filepath, url, directory, timeout, headers):
        local_filepath = os.path.join('git_repo', filepath)
        if os.path.isfile(os.path.join(directory, local_filepath)) and not filepath.endswith('/'):
            printf("[-] Already downloaded %s/%s\n", url, filepath)
            return []

        # Ensure URL has a trailing slash for proper joining, but don't modify the base url arg
        full_request_url = url.rstrip('/') + '/' + filepath.lstrip('/')

        with self.session.get(full_request_url, stream=True, timeout=timeout) as response:
            printf("[-] Fetching %s [%d]\n", full_request_url, response.status_code)

            if (
                response.status_code in (301, 302)
                and "Location" in response.headers
                and response.headers["Location"].endswith(filepath + "/")
            ):
                return [filepath + "/"]

            # Treat empty filepath as a directory index as well
            if filepath.endswith("/") or filepath == '':
                if response.status_code == 200 and is_html(response):
                    return [
                        filepath + filename
                        for filename in get_indexed_files(response)
                    ]
                return []
            else:  # file
                valid, error_message = verify_response(response)
                if not valid:
                    printf(error_message % (url, filepath), file=sys.stderr)
                    return []

                abspath = os.path.abspath(os.path.join(directory, local_filepath))
                create_intermediate_dirs(abspath)
                with open(abspath, "wb") as f:
                    for chunk in response.iter_content(4096):
                        f.write(chunk)
                return []

class FindRefsWorker(DownloadWorker):
    def do_task(self, filepath, url, directory, timeout, headers):
        response = self.session.get(f"{url}/{filepath}", timeout=timeout)
        printf("[-] Fetching %s/%s [%d]\n", url, filepath, response.status_code)
        valid, error_message = verify_response(response)
        if not valid:
            printf(error_message % (url, filepath), file=sys.stderr)
            return []

        local_filepath = os.path.join('git_repo', filepath)
        abspath = os.path.abspath(os.path.join(directory, local_filepath))
        create_intermediate_dirs(abspath)
        with open(abspath, "w", errors='ignore') as f:
            f.write(response.text)

        tasks = []
        for ref in re.findall(r"(refs(/[a-zA-Z0-9\-\.\_\*]+)+)", response.text):
            ref = ref[0]
            if not ref.endswith("*"):
                tasks.append(ref)
                tasks.append(f"logs/{ref}")
        return tasks

class FindObjectsWorker(DownloadWorker):
    def do_task(self, obj, url, directory, timeout, headers):
        filepath = f"objects/{obj[:2]}/{obj[2:]}"
        local_filepath = os.path.join('git_repo', filepath)
        abspath = os.path.abspath(os.path.join(directory, local_filepath))

        if os.path.isfile(abspath):
            printf("[-] Already downloaded %s/%s\n", url, filepath)
        else:
            response = self.session.get(f"{url}/{filepath}", timeout=timeout)
            printf("[-] Fetching %s/%s [%d]\n", url, filepath, response.status_code)
            valid, error_message = verify_response(response)
            if not valid:
                printf(error_message % (url, filepath), file=sys.stderr)
                return []
            create_intermediate_dirs(abspath)
            with open(abspath, "wb") as f:
                f.write(response.content)

        try:
            obj_file = dulwich.objects.ShaFile.from_path(abspath)
            return get_referenced_sha1(obj_file)
        except Exception as e:
            printf(f"Warning: Could not parse object {obj}: {e}\n", file=sys.stderr)
            return []

def download_git_repo(url, output_dir, jobs=10, timeout=10):
    """ Dumps a git repository into the output directory using git-dumper's logic. """
    if not output_dir:
        output_dir = url.split('/')[-2]
        if output_dir.endswith('.git'):
            output_dir = output_dir[:-4]

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    elif not os.path.isdir(output_dir):
        printf(f"Error: Destination '{output_dir}' exists and is not a directory.\n", file=sys.stderr)
    elif os.listdir(output_dir):
        printf("Warning: Destination '%s' is not empty\n", output_dir)

    # Normalize URL
    url = url.rstrip("/")
    if url.endswith("HEAD"): url = url[:-4]
    url = url.rstrip("/")
    if not url.endswith(".git"):
        printf("Warning: URL does not end with .git. Appending '/.git'\n")
        url += "/.git"

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; rv:78.0) Gecko/20100101 Firefox/78.0'}
    session = requests.Session()
    session.headers = headers
    session.verify = False

    printf("[-] Testing %s/HEAD ", url)
    response = session.get(f"{url}/HEAD", timeout=timeout, allow_redirects=False)
    printf("[%d]\n", response.status_code)
    valid, error_message = verify_response(response)
    if not valid or not re.match(r"^(ref:.*|[0-9a-f]{40}$)", response.text.strip()):
        printf(f"Error: {url}/HEAD is not a valid git HEAD file.\n", file=sys.stderr)
        return

    # Check for directory listing
    dir_listing_url = url + '/'
    printf("[-] Testing %s for directory listing ", dir_listing_url)
    response = session.get(dir_listing_url, allow_redirects=False, timeout=timeout)
    printf("[%d]\n", response.status_code)

    # Handle 301 redirect to the same URL with a trailing slash
    if response.status_code == 301 and response.headers.get("Location") == dir_listing_url:
        printf("[-] Following redirect to %s ", dir_listing_url)
        url = dir_listing_url  # CRITICAL FIX: Update the url variable
        response = session.get(dir_listing_url, allow_redirects=False, timeout=timeout)
        printf("[%d]\n", response.status_code)
    elif response.status_code == 200 and not url.endswith('/'):
        # Some servers might return 200 on a directory without a slash. Let's ensure we use the slashed version.
        url += '/'

    if (
        response.status_code == 200
        and is_html(response)
        and "HEAD" in get_indexed_files(response)
    ):
        printf("[-] Directory listing detected. Fetching recursively...\n")
        process_tasks([''], RecursiveDownloadWorker, jobs, args=(url, output_dir, timeout, headers))
        printf("\n[*] Recursive download complete.\n")
        return

    # No directory listing support, directly to file fetching
    printf("[-] Fetching common files\n")
    common_files = [
        "COMMIT_EDITMSG", "description", "index",
        "info/exclude", "objects/info/packs"
    ]
    process_tasks(common_files, DownloadWorker, jobs, args=(url, output_dir, timeout, headers))

    printf("[-] Finding refs/\n")
    ref_files = [
        "FETCH_HEAD", "HEAD", "ORIG_HEAD", "config",
        "info/refs", "logs/HEAD", "logs/refs/heads/master",
        "logs/refs/heads/main", "packed-refs", "refs/heads/master",
        "refs/heads/main", "refs/stash"
    ]
    process_tasks(ref_files, FindRefsWorker, jobs, args=(url, output_dir, timeout, headers))

    printf("[-] Finding packs\n")
    pack_tasks = []
    info_packs_path = os.path.join(output_dir, "git_repo", "objects", "info", "packs")
    if os.path.exists(info_packs_path):
        with open(info_packs_path, "r") as f:
            for sha1 in re.findall(r"pack-([a-f0-9]{40})\.pack", f.read()):
                pack_tasks.append(f"objects/pack/pack-{sha1}.idx")
                pack_tasks.append(f"objects/pack/pack-{sha1}.pack")
    process_tasks(pack_tasks, DownloadWorker, jobs, args=(url, output_dir, timeout, headers))

    printf("[-] Finding objects\n")
    objs = set()
    packed_objs = set()
    git_repo_dir = os.path.join(output_dir, "git_repo")

    files_to_scan = [
        os.path.join(git_repo_dir, "packed-refs"), os.path.join(git_repo_dir, "info", "refs"),
        os.path.join(git_repo_dir, "FETCH_HEAD"), os.path.join(git_repo_dir, "ORIG_HEAD"),
    ]
    for d in ["refs", "logs"]:
        for dirpath, _, filenames in os.walk(os.path.join(git_repo_dir, d)):
            for filename in filenames:
                files_to_scan.append(os.path.join(dirpath, filename))

    for filepath in files_to_scan:
        if os.path.exists(filepath):
            with open(filepath, "r", errors='ignore') as f:
                for obj in re.findall(r"(?:^|\s)([a-f0-9]{40})(?:$|\s)", f.read()):
                    objs.add(obj)

    index_path = os.path.join(git_repo_dir, "index")
    if os.path.exists(index_path):
        # iterobjects() yields a tuple with more than 2 values.
        for entry in dulwich.index.Index(index_path).iterobjects():
            objs.add(entry[1].decode())

    pack_dir = os.path.join(git_repo_dir, "objects", "pack")
    if os.path.isdir(pack_dir):
        for filename in os.listdir(pack_dir):
            if filename.endswith(".pack"):
                pack_data_path = os.path.join(pack_dir, filename)
                pack_idx_path = pack_data_path[:-5] + ".idx"
                if not os.path.exists(pack_idx_path): continue
                pack_data = dulwich.pack.PackData(pack_data_path)
                pack_idx = dulwich.pack.load_pack_index(pack_idx_path)
                pack = dulwich.pack.Pack.from_objects(pack_data, pack_idx)
                for obj_file in pack.iterobjects():
                    packed_objs.add(obj_file.sha().hexdigest())
                    objs.update(get_referenced_sha1(obj_file))

    printf("[-] Fetching objects\n")
    process_tasks(objs, FindObjectsWorker, jobs, args=(url, output_dir, timeout, headers), tasks_done=packed_objs)

    printf("[-] Sanitizing git_repo/config\n")
    sanitize_file(os.path.join(git_repo_dir, "config"))

    printf("\n[*] Download complete. You can now analyze the repository locally.\n")
    printf(f"[*] Example: python {sys.argv[0]} analyze \"{git_repo_dir}\" info\n")

# --- End of git-dumper integration ---

def main():
    parser = argparse.ArgumentParser(description="A tool to analyze and download local .git repositories.")
    subparsers = parser.add_subparsers(dest='command', required=True, help='Main command')

    # Analyze command
    parser_analyze = subparsers.add_parser('analyze', help='Analyze a local .git repository.')
    parser_analyze.add_argument('git_dir', help="Path to the .git directory.")
    analyze_subparsers = parser_analyze.add_subparsers(dest='subcommand', required=True, help='Analysis subcommand')

    # Info command
    parser_info = analyze_subparsers.add_parser('info', help='Display basic repository info (branches, tags).')

    # Log command
    parser_log = analyze_subparsers.add_parser('log', help='Show commit history for a branch.')
    parser_log.add_argument('branch', help='The branch to show the log for.')

    # ls-tree command
    parser_ls_tree = analyze_subparsers.add_parser('ls-tree', help='List files and directories in a commit.')
    parser_ls_tree.add_argument('ref', help='A commit SHA, branch, or tag.')
    parser_ls_tree.add_argument('-r', '--recursive', action='store_true', help='List files in subdirectories as well.')

    # History command
    parser_history = analyze_subparsers.add_parser('history', help="List all files found in the repository's history.")
    parser_history.add_argument('--all', action='store_true', help='List all file occurrences in every commit (can be very long).')

    # cat-file command
    parser_cat_file = analyze_subparsers.add_parser('cat-file', help='Show content of a git object.')
    parser_cat_file.add_argument('sha', help='The SHA-1 of the object.')

    # Recover command
    parser_recover = analyze_subparsers.add_parser('recover', help='Reconstruct all files from a commit.')
    parser_recover.add_argument('ref', help='The commit SHA, branch, or tag to recover from.')
    parser_recover.add_argument('output_dir', help='The directory to write the recovered files to.')

    # Diff command
    parser_diff = analyze_subparsers.add_parser('diff', help='Show changes between two commits, or the changes in a single commit.')
    parser_diff.add_argument('ref1', help='The first commit SHA, branch, or tag.')
    parser_diff.add_argument('ref2', nargs='?', help='The second commit SHA, branch, or tag. If not provided, shows changes in ref1 compared to its parent.')

    # Download command
    parser_download = subparsers.add_parser('download', help='Download a .git repository from a URL.')
    parser_download.add_argument('url', help='The URL of the .git directory to download.')
    parser_download.add_argument('output_dir', help='The directory to save the repository to.')
    parser_download.add_argument('-j', '--jobs', type=int, default=10, help='Number of simultaneous requests.')
    parser_download.add_argument('-t', '--timeout', type=int, default=10, help='Request timeout in seconds.')

    args = parser.parse_args()

    try:
        if args.command == 'analyze':
            if args.subcommand == 'info':
                analyzer = GitAnalyzer(args.git_dir)
                analyzer.info()
            elif args.subcommand == 'log':
                analyzer = GitAnalyzer(args.git_dir)
                analyzer.log(args.branch)
            elif args.subcommand == 'ls-tree':
                analyzer = GitAnalyzer(args.git_dir)
                analyzer.ls_tree(args.ref, args.recursive)
            elif args.subcommand == 'history':
                analyzer = GitAnalyzer(args.git_dir)
                analyzer.history(args.all)
            elif args.subcommand == 'cat-file':
                analyzer = GitAnalyzer(args.git_dir)
                analyzer.cat_file(args.sha)
            elif args.subcommand == 'recover':
                analyzer = GitAnalyzer(args.git_dir)
                analyzer.recover(args.ref, args.output_dir)
            elif args.subcommand == 'diff':
                analyzer = GitAnalyzer(args.git_dir)
                analyzer.diff(args.ref1, args.ref2)
        elif args.command == 'download':
            download_git_repo(args.url, args.output_dir, args.jobs, args.timeout)

    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()

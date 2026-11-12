"""Windows-only diagnostic probe for #238 (NOT a security verification).

This test records what the Windows runner actually does with directory-relative
``NtCreateFile`` calls.  It deliberately does **not** assert that any variant is
safe and it does not implement a production scanner.  The output line beginning
with ``WINPROBE`` is the evidence.  On Linux/macOS the test takes a verifiable
platform-unavailable branch that reports ``NOT_RUN``; that run is not pass
evidence for the Windows probe and must not be described as one.

Probe construction and evidence boundaries:

* The fixture is an isolated temporary ``root`` plus an isolated ``outside``
  canary directory, both created with ``tempfile.mkdtemp``.  Cleanup is entered
  in a ``finally`` block as soon as the temp directory exists.
* No shell and no command interpreter anywhere: the junction is created with
  ``DeviceIoControl(FSCTL_SET_REPARSE_POINT)`` and the symlink with
  ``os.symlink``.  Temporary paths never reach a command line, so shell
  metacharacters in them are inert.  There is no ``cmd /c mklink`` fallback: a
  junction that cannot be created natively is reported ``NOT_RUN`` for that
  target instead of being attempted unsafely.
* Cleanup removes only entries this probe created, using non-recursive
  ``unlink``/``rmdir`` calls.  Nothing recurses, so a reparse point swapped in
  concurrently cannot redirect deletion outside the probe's own temp tree.
  Cleanup failures are recorded as sanitized codes and fail the test instead of
  being ignored; no record contains a local path or file content.
* Content reading is a design boundary only: the probe never issues
  ``ReadFile``/``NtReadFile`` and reports ``content_read: NOT_ATTEMPTED``.  That
  is not an observation that a canary stayed unread and must not be cited as
  one.  Only NTSTATUS, handle attributes/tag and handle-close results are
  measured.
* Every state is recorded as what was observed: a real NTSTATUS, an exception
  name, "no handle was produced", or the actual ``NtClose`` result.  A close is
  never reported as successful without being observed.
* A junction/symlink that cannot be created is reported ``NOT_RUN`` for that
  target; it is never counted as a successful variant.
* The handle's final path label is **not** reported.  The earlier
  ``final_under_root``/``final_under_outside`` labels were unreliable (they read
  ``no`` even for handles opened under the probe root), so both fields are
  ``UNKNOWN`` here and no conclusion may be drawn from them.
* No network, no third-party dependency, no production code and no workflow
  change.
"""
from __future__ import annotations

import ast
import ctypes
import json
import os
import platform
import struct
import sys
import tempfile
import unittest

if os.name == 'nt':  # pragma: no branch - the whole probe is Windows-only
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    ntdll = ctypes.WinDLL('ntdll')
    print(f'WINPROBE-IMPORT os.name={os.name!r} platform={platform.platform()!r}',
          file=sys.stderr, flush=True)
else:  # Linux/macOS: the test runs a NOT_RUN platform branch, no Windows API load
    kernel32 = None
    ntdll = None

# Number of times the Windows API surface was configured in this process.  This
# is not a count of API calls; a non-Windows run must leave it at 0.
API_CONFIGURATIONS = 0

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_READ_ATTRIBUTES = 0x00000080
FILE_TRAVERSE = 0x00000020
FILE_LIST_DIRECTORY = 0x00000001
SYNCHRONIZE = 0x00100000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
FILE_OPEN = 0x00000001
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
CREATE_DISPOSITION_OPEN = 1

FILE_DIRECTORY_FILE = 0x00000001
FILE_NON_DIRECTORY_FILE = 0x00000040
FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
FILE_OPEN_FOR_BACKUP_INTENT = 0x00004000
FILE_OPEN_REPARSE_POINT = 0x00200000

OBJ_CASE_INSENSITIVE = 0x00000040
OBJ_DONT_REPARSE = 0x00001000

FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

FSCTL_SET_REPARSE_POINT = 0x000900A4
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ('Length', ctypes.c_ushort),
        ('MaximumLength', ctypes.c_ushort),
        ('Buffer', ctypes.c_void_p),
    ]


class OBJECT_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ('Length', ctypes.c_ulong),
        ('RootDirectory', ctypes.c_void_p),
        ('ObjectName', ctypes.c_void_p),
        ('Attributes', ctypes.c_ulong),
        ('SecurityDescriptor', ctypes.c_void_p),
        ('SecurityQualityOfService', ctypes.c_void_p),
    ]


class IO_STATUS_BLOCK(ctypes.Structure):
    _fields_ = [
        ('Status', ctypes.c_void_p),
        ('Information', ctypes.c_size_t),
    ]


class FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
    _fields_ = [
        ('FileAttributes', ctypes.c_uint32),
        ('ReparseTag', ctypes.c_uint32),
    ]


def _configure_api():
    global API_CONFIGURATIONS
    API_CONFIGURATIONS += 1
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.GetFileInformationByHandleEx.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
    ]
    kernel32.GetFileInformationByHandleEx.restype = ctypes.c_int
    kernel32.GetVolumeInformationW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_wchar_p, ctypes.c_uint32,
    ]
    kernel32.GetVolumeInformationW.restype = ctypes.c_int
    kernel32.DeviceIoControl.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
    ]
    kernel32.DeviceIoControl.restype = ctypes.c_int
    ntdll.NtCreateFile.argtypes = [
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32,
        ctypes.POINTER(OBJECT_ATTRIBUTES), ctypes.POINTER(IO_STATUS_BLOCK),
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
    ]
    ntdll.NtCreateFile.restype = ctypes.c_int32
    ntdll.NtClose.argtypes = [ctypes.c_void_p]
    ntdll.NtClose.restype = ctypes.c_int32


def _open_root(path):
    """Open the probe root; returns ``(handle, error_code)``.

    ``error_code`` is the real ``GetLastError`` value when no handle came back.
    """
    handle = kernel32.CreateFileW(
        str(path),
        FILE_READ_ATTRIBUTES | FILE_TRAVERSE | FILE_LIST_DIRECTORY | SYNCHRONIZE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle in (None, INVALID_HANDLE_VALUE):
        return None, ctypes.get_last_error()
    return handle, None


def _attribute_tag(handle):
    info = FILE_ATTRIBUTE_TAG_INFO()
    ok = kernel32.GetFileInformationByHandleEx(
        handle, FILE_ATTRIBUTE_TAG_INFO_CLASS, ctypes.byref(info), ctypes.sizeof(info))
    if not ok:
        return None
    return info.FileAttributes, info.ReparseTag


def _ntcreate(handle_out, root_handle, name, create_options, attributes):
    name_buffer = ctypes.create_unicode_buffer(name)
    us = UNICODE_STRING()
    us.Length = len(name.encode('utf-16-le'))
    us.MaximumLength = us.Length + ctypes.sizeof(ctypes.c_wchar)
    us.Buffer = ctypes.cast(name_buffer, ctypes.c_void_p)
    oa = OBJECT_ATTRIBUTES()
    oa.Length = ctypes.sizeof(OBJECT_ATTRIBUTES)
    oa.RootDirectory = ctypes.c_void_p(root_handle)
    oa.ObjectName = ctypes.cast(ctypes.pointer(us), ctypes.c_void_p)
    oa.Attributes = attributes
    iosb = IO_STATUS_BLOCK()
    status = ntdll.NtCreateFile(
        ctypes.byref(handle_out),
        FILE_READ_ATTRIBUTES | FILE_TRAVERSE | SYNCHRONIZE,
        ctypes.byref(oa),
        ctypes.byref(iosb),
        None,
        0,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        CREATE_DISPOSITION_OPEN,
        create_options,
        None,
        0,
    )
    return status


def _run_variant(root_handle, name, variant, create_options, attributes):
    handle = ctypes.c_void_p()
    result = {
        'target': name,
        'variant': variant,
        'create_options': hex(create_options),
        'attributes': hex(attributes),
        'status': None,
        'status_hex': None,
        'exception': None,
        'success': False,
        'handle_opened': False,
        'is_directory': None,
        'is_reparse': None,
        'reparse_tag': None,
        'tag_info_error': None,
        # Final-path containment is deliberately not reported; the previous
        # labels were unreliable, so these stay UNKNOWN (see module docstring).
        'final_under_root': 'UNKNOWN',
        'final_under_outside': 'UNKNOWN',
        'closed': None,
        'close_status_hex': None,
        'anomaly': None,
    }
    try:
        status = _ntcreate(handle, root_handle, name, create_options, attributes)
    except Exception as exc:  # noqa: BLE001 - diagnostic must record, not crash
        result['exception'] = type(exc).__name__
        return result
    result['status'] = int(status)
    result['status_hex'] = f'0x{status & 0xffffffff:08x}'
    opened = handle.value not in (None, 0)
    result['handle_opened'] = opened
    if status >= 0 and opened:
        result['success'] = True
        attrs = _attribute_tag(handle)
        if attrs is None:
            result['tag_info_error'] = ctypes.get_last_error()
        else:
            file_attributes, reparse_tag = attrs
            result['is_directory'] = bool(file_attributes & FILE_ATTRIBUTE_DIRECTORY)
            result['is_reparse'] = bool(file_attributes & FILE_ATTRIBUTE_REPARSE_POINT)
            result['reparse_tag'] = hex(reparse_tag)
    elif status >= 0:
        # A success status without a handle is an anomaly, not a close.
        result['anomaly'] = 'success_without_handle'
    if opened:
        try:
            close_status = ntdll.NtClose(handle)
        except Exception:  # noqa: BLE001 - diagnostic
            result['closed'] = False
        else:
            result['close_status_hex'] = f'0x{close_status & 0xffffffff:08x}'
            result['closed'] = close_status == 0
    return result


def _mount_point_buffer(target):
    """Build the FSCTL_SET_REPARSE_POINT input for an NTFS junction.

    Layout: REPARSE_DATA_BUFFER header (tag + data length + reserved = 8 bytes)
    then MOUNT_POINT_REPARSE_BUFFER (four USHORT fields = 8 bytes, no Flags)
    then the substitute and print names, both counted in bytes.
    """
    substitute = ('\\??\\' + str(target)).encode('utf-16-le')
    printable_name = str(target).encode('utf-16-le')
    path_buffer_offset = 16  # 8-byte header + 8-byte mount-point fields
    data_length = 8 + len(substitute) + len(printable_name)
    size = path_buffer_offset + len(substitute) + len(printable_name)
    buffer = ctypes.create_string_buffer(size)
    struct.pack_into('<IHH', buffer, 0, IO_REPARSE_TAG_MOUNT_POINT, data_length, 0)
    struct.pack_into('<HHHH', buffer, 8, 0, len(substitute),
                     len(substitute), len(printable_name))
    buffer[path_buffer_offset:path_buffer_offset + len(substitute)] = substitute
    start = path_buffer_offset + len(substitute)
    buffer[start:start + len(printable_name)] = printable_name
    return buffer


def _create_junction(link, target):
    """Create an NTFS mount-point junction without any shell in the path.

    Returns ``(created, reason)`` where ``reason`` is a sanitized code string or
    ``None``.  Paths are only ever passed to Win32 APIs, so metacharacters in a
    temporary path are inert.  There is deliberately no ``cmd /c mklink``
    fallback: if this native path fails, the caller reports ``NOT_RUN``.
    """
    try:
        os.makedirs(link)
    except OSError as exc:
        return False, f'makedirs:{getattr(exc, "winerror", None) or exc.errno}'
    handle = kernel32.CreateFileW(
        str(link),
        GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle in (None, INVALID_HANDLE_VALUE):
        return False, f'CreateFileW:0x{ctypes.get_last_error():08x}'
    try:
        buffer = _mount_point_buffer(target)
        returned = ctypes.c_uint32(0)
        ok = kernel32.DeviceIoControl(
            handle, FSCTL_SET_REPARSE_POINT, buffer, ctypes.sizeof(buffer),
            None, 0, ctypes.byref(returned), None)
        if not ok:
            return False, f'FSCTL_SET_REPARSE_POINT:0x{ctypes.get_last_error():08x}'
        tag = getattr(os.lstat(link), 'st_reparse_tag', None)
        if tag != IO_REPARSE_TAG_MOUNT_POINT:
            return False, 'tag-mismatch'
        return True, None
    except OSError as exc:
        return False, f'lstat:{getattr(exc, "winerror", None) or exc.errno}'
    finally:
        kernel32.CloseHandle(handle)


def _create_symlink(link, target):
    """Create a directory symlink with ``os.symlink`` (no shell).

    Returns ``(created, reason)`` with a sanitized reason on failure.
    """
    try:
        os.symlink(target, link, target_is_directory=True)
        return True, None
    except OSError as exc:
        code = getattr(exc, 'winerror', None) or exc.errno
        return False, f'os.symlink:{code}'


def _remove_entry(path, label, errors):
    """Remove one probe-created entry without recursing or following reparse points.

    ``unlink`` then ``rmdir`` cover files, empty directories, symlinks and
    junctions; both are non-recursive, so a concurrently swapped reparse point
    cannot redirect deletion outside the probe's own temp tree.  The recorded
    failure uses a fixed label and a numeric code, never a local path.
    """
    last = None
    for remover in (os.unlink, os.rmdir):
        try:
            remover(path)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            last = exc
    code = getattr(last, 'winerror', None)
    if code is None and last is not None:
        code = last.errno
    errors.append(f'{label}:{type(last).__name__}:{code}')


def _cleanup_probe_temp(temp, root, outside):
    """Remove only this probe's artifacts, bottom-up, and return sanitized errors."""
    errors = []
    for label, path in (
        ('linkdir', os.path.join(root, 'linkdir')),
        ('symlinkdir', os.path.join(root, 'symlinkdir')),
        ('inside.txt', os.path.join(root, 'sub', 'inside.txt')),
        ('canary.txt', os.path.join(outside, 'canary.txt')),
        ('sub', os.path.join(root, 'sub')),
        ('child', os.path.join(outside, 'child')),
        ('outside', outside),
        ('root', root),
        ('temp', temp),
    ):
        _remove_entry(path, label, errors)
    return errors


def _run_probe():
    _configure_api()
    report = {
        'probe': 'completed',
        'diagnostic_only': True,
        'platform': platform.platform(),
        'python': sys.version.split()[0],
        'windows_version': str(sys.getwindowsversion()),
        'filesystem': 'UNKNOWN',
        'api_configurations': API_CONFIGURATIONS,
        'root_open': False,
        'root_open_error': None,
        'root_closed': None,
        'junction_created': 'NOT_RUN',
        'symlink_created': 'NOT_RUN',
        'content_read': 'NOT_ATTEMPTED',
        'handles_closed': False,
        'close_failures': [],
        'anomalies': [],
        'cleanup_errors': [],
        'variants': [],
        'unknown': [],
    }
    temp = tempfile.mkdtemp(prefix='agentorch-winprobe-')
    root = os.path.join(temp, 'root')
    outside = os.path.join(temp, 'outside')
    try:
        os.makedirs(root)
        os.makedirs(os.path.join(outside, 'child'))
        # Isolated canary target inside the probe's own temp dir.  It is written
        # here and never read; `content_read: NOT_ATTEMPTED` is a design
        # boundary, not an observation.
        with open(os.path.join(outside, 'canary.txt'), 'w', encoding='utf-8') as handle:
            handle.write('WINPROBE_CANARY_NEVER_READ')

        drive = os.path.splitdrive(root)[0] + '\\'
        fs_buffer = ctypes.create_unicode_buffer(256)
        try:
            if kernel32.GetVolumeInformationW(
                    drive, None, 0, None, None, None, fs_buffer, len(fs_buffer)):
                report['filesystem'] = fs_buffer.value or 'UNKNOWN'
        except Exception:  # noqa: BLE001 - diagnostic
            report['filesystem'] = 'UNKNOWN'

        root_handle, root_error = _open_root(root)
        if root_handle is None:
            # Real failure code; no handle exists, so nothing is closed or
            # claimed as closed.
            report['root_open_error'] = root_error
            return report
        report['root_open'] = True
        try:
            os.makedirs(os.path.join(root, 'sub'))
            with open(os.path.join(root, 'sub', 'inside.txt'), 'w', encoding='utf-8') as fh:
                fh.write('inside\n')

            junction = os.path.join(root, 'linkdir')
            created, reason = _create_junction(junction, outside)
            if created:
                report['junction_created'] = 'yes'
            else:
                report['junction_created'] = 'NOT_RUN'
                report['unknown'].append(f'junction {reason}')

            symlink = os.path.join(root, 'symlinkdir')
            created, reason = _create_symlink(symlink, outside)
            if created:
                report['symlink_created'] = 'yes'
            else:
                report['symlink_created'] = 'NOT_RUN'
                report['unknown'].append(f'symlink {reason}')

            variants = [
                ('V1-directory+open-reparse',
                 FILE_DIRECTORY_FILE | FILE_OPEN_REPARSE_POINT |
                 FILE_OPEN_FOR_BACKUP_INTENT | FILE_SYNCHRONOUS_IO_NONALERT,
                 OBJ_CASE_INSENSITIVE),
                ('V2-open-reparse-no-directory',
                 FILE_OPEN_REPARSE_POINT | FILE_OPEN_FOR_BACKUP_INTENT |
                 FILE_SYNCHRONOUS_IO_NONALERT,
                 OBJ_CASE_INSENSITIVE),
                ('V3-directory+dont-reparse',
                 FILE_DIRECTORY_FILE | FILE_OPEN_FOR_BACKUP_INTENT |
                 FILE_SYNCHRONOUS_IO_NONALERT,
                 OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE),
                ('V4-directory+open-reparse+dont-reparse',
                 FILE_DIRECTORY_FILE | FILE_OPEN_REPARSE_POINT |
                 FILE_OPEN_FOR_BACKUP_INTENT | FILE_SYNCHRONOUS_IO_NONALERT,
                 OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE),
            ]
            targets = ['sub']
            if report['junction_created'] == 'yes':
                # Relative NtCreateFile object names use the Windows separator;
                # 'linkdir/child' returns STATUS_OBJECT_NAME_INVALID.
                targets.extend(['linkdir', 'linkdir\\child'])
            if report['symlink_created'] == 'yes':
                targets.append('symlinkdir')
            for target in targets:
                for variant, options, attributes in variants:
                    report['variants'].append(
                        _run_variant(root_handle, target, variant, options, attributes))

            report['anomalies'] = [
                f"{item['target']}/{item['variant']}:{item['anomaly']}"
                for item in report['variants'] if item['anomaly']
            ]
            report['close_failures'] = [
                f"{item['target']}/{item['variant']}"
                for item in report['variants'] if item['closed'] is False
            ]
        finally:
            closed = kernel32.CloseHandle(root_handle)
            report['root_closed'] = bool(closed)
        report['handles_closed'] = (
            report['root_closed'] is True
            and not report['close_failures']
            and all(item['closed'] is True
                    for item in report['variants'] if item['handle_opened'])
        )
    finally:
        report['cleanup_errors'] = _cleanup_probe_temp(temp, root, outside)
    return report


class WindowsNtCreateFileProbe(unittest.TestCase):
    def test_ntcreatefile_root_relative_probe(self):
        if os.name != 'nt':
            # Verified platform-unavailable branch.  It must run (not skip) and
            # prove that no Windows API was loaded or configured, so the run is
            # NOT_RUN and never pass evidence for the Windows probe.
            self.assertIsNone(kernel32, 'Windows API must not load off Windows')
            self.assertIsNone(ntdll, 'Windows API must not load off Windows')
            self.assertEqual(API_CONFIGURATIONS, 0,
                             'no Windows API may be configured off Windows')
            print(f'WINPROBE NOT_RUN content_read=NOT_ATTEMPTED '
                  f'os.name={os.name!r} platform={platform.platform()!r}',
                  file=sys.stderr, flush=True)
            return
        report = _run_probe()
        print('WINPROBE ' + json.dumps(report, sort_keys=True, ensure_ascii=False),
              file=sys.stderr, flush=True)
        self.assertEqual(report.get('probe'), 'completed')
        self.assertTrue(report.get('root_open'), 'WINPROBE root handle did not open')
        self.assertIsNone(report.get('root_open_error'),
                          'WINPROBE reported both root_open and a root error')
        self.assertIs(report.get('root_closed'), True,
                      'WINPROBE root handle close was not observed')
        self.assertTrue(report.get('handles_closed'), 'WINPROBE handle leak')
        self.assertEqual(report.get('close_failures'), [], 'WINPROBE close failures')
        self.assertEqual(report.get('anomalies'), [], 'WINPROBE anomalies')
        self.assertEqual(report.get('cleanup_errors'), [], 'WINPROBE cleanup errors')
        self.assertEqual(report.get('content_read'), 'NOT_ATTEMPTED',
                         'WINPROBE content-reading boundary changed')
        self.assertGreaterEqual(report.get('api_configurations'), 1,
                                'WINPROBE never configured the Windows API')
        variants = report.get('variants') or []
        self.assertTrue(variants, 'WINPROBE recorded no variants')
        for item in variants:
            self.assertIsNone(item.get('exception'), f'WINPROBE exception: {item}')
            self.assertEqual(item.get('final_under_root'), 'UNKNOWN')
            self.assertEqual(item.get('final_under_outside'), 'UNKNOWN')
            if item.get('handle_opened'):
                self.assertIs(item.get('closed'), True,
                              f'WINPROBE close not observed: {item}')
                self.assertIsNone(item.get('tag_info_error'),
                                  f'WINPROBE tag query failed: {item}')
                # An opened handle must yield real attribute/tag data.
                self.assertIsNotNone(item.get('is_directory'))
                self.assertIsNotNone(item.get('is_reparse'))
                self.assertIsNotNone(item.get('reparse_tag'))
                self.assertRegex(item.get('status_hex') or '', r'^0x[0-9a-f]{8}$')
            else:
                # No handle exists, so no close may be claimed.
                self.assertIsNone(item.get('closed'))
                self.assertFalse(item.get('success'))
            if item.get('success'):
                self.assertTrue(item.get('handle_opened'))


class ProbeSelfCheck(unittest.TestCase):
    """Regression checks for the probe's own safety properties (cross-platform)."""

    def test_module_does_not_use_a_shell(self):
        with open(__file__, encoding='utf-8') as source_file:
            tree = ast.parse(source_file.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split('.')[0])
        self.assertNotIn('subprocess', imported,
                         'probe must not use a command interpreter')
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', '')
            self.assertNotIn(name, {'system', 'popen'},
                             'probe must not spawn a shell')
            for keyword in node.keywords:
                self.assertNotEqual(keyword.arg, 'shell',
                                    'probe must not pass shell=True anywhere')

    def test_remove_entry_never_recurses(self):
        temp = tempfile.mkdtemp(prefix='agentorch-probe-entry-')
        target = os.path.join(temp, 'dir')
        nested = os.path.join(target, 'nested.txt')
        try:
            os.makedirs(target)
            with open(nested, 'w', encoding='utf-8') as handle:
                handle.write('x')
            errors = []
            _remove_entry(target, 'dir', errors)
            self.assertEqual(len(errors), 1)
            self.assertTrue(errors[0].startswith('dir:'))
            self.assertNotIn(temp, errors[0], 'error text must not leak a local path')
            self.assertTrue(os.path.exists(nested), 'removal recursed into a directory')
        finally:
            _remove_entry(nested, 'test', [])
            _remove_entry(target, 'test', [])
            _remove_entry(temp, 'test', [])

    def test_cleanup_reports_leftovers_instead_of_deleting_them(self):
        temp = tempfile.mkdtemp(prefix='agentorch-probe-leftover-')
        root = os.path.join(temp, 'root')
        outside = os.path.join(temp, 'outside')
        leftover = os.path.join(root, 'unexpected.txt')
        try:
            os.makedirs(os.path.join(root, 'sub'))
            os.makedirs(os.path.join(outside, 'child'))
            for path in (os.path.join(root, 'sub', 'inside.txt'),
                         os.path.join(outside, 'canary.txt'),
                         leftover):
                with open(path, 'w', encoding='utf-8') as handle:
                    handle.write('x')
            errors = _cleanup_probe_temp(temp, root, outside)
            self.assertTrue(errors, 'leftover content was removed silently')
            self.assertTrue(os.path.exists(leftover))
            self.assertNotIn(temp, ' '.join(errors),
                             'error text must not leak a local path')
            self.assertTrue(any(error.startswith('root:') for error in errors))
            os.unlink(leftover)
            self.assertEqual(_cleanup_probe_temp(temp, root, outside), [])
            self.assertFalse(os.path.exists(temp))
        finally:
            _remove_entry(leftover, 'test', [])
            _remove_entry(os.path.join(root, 'sub', 'inside.txt'), 'test', [])
            _remove_entry(os.path.join(outside, 'canary.txt'), 'test', [])
            _remove_entry(os.path.join(root, 'sub'), 'test', [])
            _remove_entry(os.path.join(outside, 'child'), 'test', [])
            _remove_entry(outside, 'test', [])
            _remove_entry(root, 'test', [])
            _remove_entry(temp, 'test', [])

    def test_junction_buffer_matches_mount_point_layout(self):
        target = 'C:\\probe target\\outside'
        raw = bytes(_mount_point_buffer(target))
        tag, data_length, reserved = struct.unpack_from('<IHH', raw, 0)
        self.assertEqual(tag, IO_REPARSE_TAG_MOUNT_POINT)
        self.assertEqual(reserved, 0)
        sub_offset, sub_length, print_offset, print_length = struct.unpack_from(
            '<HHHH', raw, 8)
        self.assertEqual(sub_offset, 0)
        self.assertEqual(print_offset, sub_length)
        self.assertEqual(data_length, 8 + sub_length + print_length)
        self.assertEqual(len(raw), 16 + sub_length + print_length)
        self.assertEqual(raw[16:16 + sub_length].decode('utf-16-le'),
                         '\\??\\' + target)
        self.assertEqual(raw[16 + sub_length:].decode('utf-16-le'), target)

    def test_missing_handle_never_reports_a_close(self):
        module = sys.modules[__name__]
        original = module._ntcreate
        try:
            # STATUS_OBJECT_NAME_NOT_FOUND: no handle is produced, so no close
            # may be claimed and the status must be reported verbatim.
            module._ntcreate = lambda *args, **kwargs: -1073741811  # 0xC000000D
            failed = module._run_variant(0, 'sub', 'V-test', 0, 0)
            self.assertFalse(failed['handle_opened'])
            self.assertFalse(failed['success'])
            self.assertIsNone(failed['closed'])
            self.assertEqual(failed['status_hex'], '0xc000000d')
            self.assertEqual(failed['final_under_root'], 'UNKNOWN')

            # A success status without a handle is an anomaly, not a close.
            module._ntcreate = lambda *args, **kwargs: 0
            anomaly = module._run_variant(0, 'sub', 'V-test', 0, 0)
            self.assertFalse(anomaly['handle_opened'])
            self.assertFalse(anomaly['success'])
            self.assertIsNone(anomaly['closed'])
            self.assertEqual(anomaly['anomaly'], 'success_without_handle')
        finally:
            module._ntcreate = original


if __name__ == '__main__':
    unittest.main()

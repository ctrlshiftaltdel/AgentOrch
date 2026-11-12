"""Windows-only diagnostic probe for #238 (NOT a security verification).

This test records what the Windows runner actually does with directory-relative
``NtCreateFile`` calls.  It deliberately does **not** assert that any variant is
safe and it does not implement a production scanner.  The output line beginning
with ``WINPROBE`` is the evidence.  On Linux/macOS the test takes a verifiable
platform-unavailable branch that reports ``NOT_RUN``; that run is not pass
evidence for the Windows probe and must not be described as one.

Safety and evidence boundaries of the probe itself:

* It creates an isolated temporary ``root`` and an isolated ``outside`` canary
  directory.  Content reading is a design boundary only: the probe never issues
  ``ReadFile``/``NtReadFile`` and reports ``content_read: NOT_ATTEMPTED``.  That
  is not an observation that a canary stayed unread and must not be cited as
  one.  Only NTSTATUS, handle attributes/tag and handle-close results are
  measured.
* Every opened handle is closed; a close failure is reported and fails the test.
* A junction/symlink that cannot be created is reported as ``UNKNOWN`` /
  ``NOT_RUN`` for that target; it is never counted as a successful variant.
* The handle's final path label is **not** reported.  The earlier
  ``final_under_root``/``final_under_outside`` labels were unreliable (they read
  ``no`` even for handles opened under the probe root), so both fields are
  ``UNKNOWN`` here and no conclusion may be drawn from them.
* No network, no third-party dependency, no production code and no workflow
  change.
"""
from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest

if os.name == 'nt':  # pragma: no branch - the whole probe is Windows-only
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    ntdll = ctypes.WinDLL('ntdll')
    print(f'WINPROBE-IMPORT os.name={os.name!r} platform={platform.platform()!r}',
          file=sys.stderr, flush=True)
else:  # Linux/macOS: the test runs a NOT_RUN platform branch, no Windows API call
    kernel32 = None
    ntdll = None

# Counts Windows API configuration/use. A non-Windows run must leave this at 0.
WINDOWS_API_CALLS = 0

GENERIC_READ = 0x80000000
FILE_READ_ATTRIBUTES = 0x00000080
FILE_TRAVERSE = 0x00000020
FILE_LIST_DIRECTORY = 0x00000001
SYNCHRONIZE = 0x00100000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
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
    global WINDOWS_API_CALLS
    WINDOWS_API_CALLS += 1
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
        return None
    return handle


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
        'success': False,
        'handle': False,
        'is_directory': None,
        'is_reparse': None,
        'reparse_tag': None,
        # Final-path containment is deliberately not reported; the previous
        # labels were unreliable, so these stay UNKNOWN (see module docstring).
        'final_under_root': 'UNKNOWN',
        'final_under_outside': 'UNKNOWN',
        'closed': None,
    }
    try:
        status = _ntcreate(handle, root_handle, name, create_options, attributes)
        result['status'] = int(status)
        result['status_hex'] = f'0x{status & 0xffffffff:08x}'
        if status >= 0 and handle.value not in (None, 0):
            result['success'] = True
            result['handle'] = True
            attrs = _attribute_tag(handle)
            if attrs is not None:
                file_attributes, reparse_tag = attrs
                result['is_directory'] = bool(file_attributes & FILE_ATTRIBUTE_DIRECTORY)
                result['is_reparse'] = bool(file_attributes & FILE_ATTRIBUTE_REPARSE_POINT)
                result['reparse_tag'] = hex(reparse_tag)
        elif status >= 0:
            result['closed'] = True
    except Exception as exc:  # noqa: BLE001 - diagnostic must record, not crash
        result['status'] = f'EXCEPTION:{type(exc).__name__}'
    finally:
        if handle.value not in (None, 0) and result.get('closed') is None:
            try:
                result['closed'] = ntdll.NtClose(handle) == 0
            except Exception:  # noqa: BLE001 - diagnostic
                result['closed'] = False
    return result


def _create_junction(link, target):
    try:
        completed = subprocess.run(
            ['cmd', '/c', 'mklink', '/J', str(link), str(target)],
            capture_output=True, text=True, timeout=30, check=False)
        return completed.returncode == 0 and os.path.lexists(link), completed.returncode
    except (OSError, subprocess.SubprocessError):
        return False, None


def _run_probe():
    _configure_api()
    report = {
        'probe': 'completed',
        'diagnostic_only': True,
        'platform': platform.platform(),
        'python': sys.version.split()[0],
        'windows_version': str(sys.getwindowsversion()),
        'filesystem': 'UNKNOWN',
        'root_open': False,
        'junction_created': 'UNKNOWN',
        'symlink_created': 'UNKNOWN',
        'content_read': 'NOT_ATTEMPTED',
        'windows_api_calls': WINDOWS_API_CALLS,
        'handles_closed': False,
        'variants': [],
        'unknown': [],
    }
    temp = tempfile.mkdtemp(prefix='agentorch-winprobe-')
    root = os.path.join(temp, 'root')
    outside = os.path.join(temp, 'outside')
    os.makedirs(root)
    os.makedirs(os.path.join(outside, 'child'))
    # Isolated canary target inside the probe's own temp dir.  It is written
    # here and never read; `content_read: NOT_ATTEMPTED` is a design boundary,
    # not an observation.
    with open(os.path.join(outside, 'canary.txt'), 'w', encoding='utf-8') as handle:
        handle.write('WINPROBE_CANARY_NEVER_READ')
    try:
        drive = os.path.splitdrive(root)[0] + '\\'
        fs_buffer = ctypes.create_unicode_buffer(256)
        try:
            if kernel32.GetVolumeInformationW(
                    drive, None, 0, None, None, None, fs_buffer, len(fs_buffer)):
                report['filesystem'] = fs_buffer.value or 'UNKNOWN'
        except Exception:  # noqa: BLE001 - diagnostic
            report['filesystem'] = 'UNKNOWN'

        root_handle = _open_root(root)
        if root_handle is None:
            return report
        report['root_open'] = True
        try:
            os.makedirs(os.path.join(root, 'sub'))
            with open(os.path.join(root, 'sub', 'inside.txt'), 'w', encoding='utf-8') as fh:
                fh.write('inside\n')
            junction = os.path.join(root, 'linkdir')
            created, rc = _create_junction(junction, outside)
            report['junction_created'] = 'yes' if created else 'UNKNOWN'
            if not created:
                report['unknown'].append(f'junction creation rc={rc}')
            symlink = os.path.join(root, 'symlinkdir')
            try:
                os.symlink(outside, symlink, target_is_directory=True)
                report['symlink_created'] = 'yes'
            except OSError as exc:
                report['symlink_created'] = 'UNKNOWN'
                report['unknown'].append(f'symlink creation {type(exc).__name__}')

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
        finally:
            closed = kernel32.CloseHandle(root_handle)
            report['handles_closed'] = bool(closed)
        report['handles_closed'] = report['handles_closed'] and all(
            item.get('closed') is not False for item in report['variants'])
    finally:
        # Remove the probe's own links first so cleanup cannot traverse them.
        for link in ('linkdir', 'symlinkdir'):
            path = os.path.join(root, link)
            try:
                os.unlink(path)
            except OSError:
                try:
                    os.rmdir(path)
                except OSError:
                    pass
        shutil.rmtree(temp, ignore_errors=True)
    return report


class WindowsNtCreateFileProbe(unittest.TestCase):
    def test_ntcreatefile_root_relative_probe(self):
        if os.name != 'nt':
            # Verified platform-unavailable branch.  It must run (not skip) and
            # prove that no Windows API was loaded or configured, so the run is
            # NOT_RUN and never pass evidence for the Windows probe.
            self.assertIsNone(kernel32, 'Windows API must not load off Windows')
            self.assertIsNone(ntdll, 'Windows API must not load off Windows')
            self.assertEqual(WINDOWS_API_CALLS, 0,
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
        self.assertTrue(report.get('handles_closed'), 'WINPROBE handle leak')
        self.assertEqual(report.get('content_read'), 'NOT_ATTEMPTED',
                         'WINPROBE content-reading boundary changed')
        self.assertGreaterEqual(report.get('windows_api_calls'), 1,
                                'WINPROBE never configured the Windows API')
        variants = report.get('variants') or []
        self.assertTrue(variants, 'WINPROBE recorded no variants')
        for item in variants:
            self.assertRegex(item.get('status_hex') or '', r'^0x[0-9a-f]{8}$')
            self.assertEqual(item.get('final_under_root'), 'UNKNOWN')
            self.assertEqual(item.get('final_under_outside'), 'UNKNOWN')
            self.assertIsNot(item.get('closed'), False,
                             f'WINPROBE handle leak: {item}')
            if item.get('success'):
                # An opened handle must yield real attribute/tag data.
                self.assertIsNotNone(item.get('is_directory'))
                self.assertIsNotNone(item.get('is_reparse'))
                self.assertIsNotNone(item.get('reparse_tag'))
                self.assertIs(item.get('closed'), True)


if __name__ == '__main__':
    unittest.main()

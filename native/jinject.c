/*
 * jinject: load jclock into a game. Built once per architecture; the build
 * must match the game's bitness.
 *
 *   jinject launch <dll> <working-dir> <exe> <command-line>
 *   jinject attach <dll> <pid>
 *
 * Prints "pid=<n>" on success.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

static int fail(const char *what)
{
    fprintf(stderr, "jinject: %s (Windows error %lu)\n", what, GetLastError());
    return 1;
}

static int inject(HANDLE process, const wchar_t *dll)
{
    SIZE_T bytes = (wcslen(dll) + 1) * sizeof(wchar_t);
    FARPROC load_library;
    HANDLE thread;
    DWORD result = 0;
    void *remote;

    remote = VirtualAllocEx(process, NULL, bytes, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!remote)
        return fail("cannot allocate memory in the game");
    if (!WriteProcessMemory(process, remote, dll, bytes, NULL))
        return fail("cannot write to the game");

    /* kernel32 sits at the same address in every process of one architecture. */
    load_library = GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "LoadLibraryW");
    thread = CreateRemoteThread(process, NULL, 0, (LPTHREAD_START_ROUTINE)load_library, remote, 0, NULL);
    if (!thread)
        return fail("cannot start a thread in the game");
    if (WaitForSingleObject(thread, 15000) != WAIT_OBJECT_0) {
        CloseHandle(thread);
        return fail("loading the clock hook timed out");
    }
    GetExitCodeThread(thread, &result);
    CloseHandle(thread);
    VirtualFreeEx(process, remote, 0, MEM_RELEASE);
    if (!result) {
        fprintf(stderr, "jinject: the game could not load the clock hook\n");
        return 1;
    }
    return 0;
}

static int same_architecture(HANDLE process)
{
    BOOL theirs = FALSE, ours = FALSE;
    if (!IsWow64Process(process, &theirs) || !IsWow64Process(GetCurrentProcess(), &ours))
        return 0;
    return theirs == ours;
}

int wmain(int argc, wchar_t **argv)
{
    if (argc == 6 && wcscmp(argv[1], L"launch") == 0) {
        STARTUPINFOW startup = {sizeof(startup)};
        PROCESS_INFORMATION info;
        wchar_t *command = _wcsdup(argv[5]);

        if (!CreateProcessW(argv[4], command, NULL, NULL, FALSE, CREATE_SUSPENDED, NULL, argv[3],
                            &startup, &info))
            return fail("cannot start the game");
        if (!same_architecture(info.hProcess) || inject(info.hProcess, argv[2])) {
            TerminateProcess(info.hProcess, 1);
            fprintf(stderr, "jinject: launch abandoned\n");
            return 1;
        }
        ResumeThread(info.hThread);
        printf("pid=%lu\n", info.dwProcessId);
        return 0;
    }

    if (argc == 4 && wcscmp(argv[1], L"attach") == 0) {
        DWORD pid = wcstoul(argv[3], NULL, 10);
        HANDLE process = OpenProcess(PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION |
                                         PROCESS_VM_OPERATION | PROCESS_VM_WRITE | PROCESS_VM_READ,
                                     FALSE, pid);
        if (!process)
            return fail("cannot open the game (is it running as administrator?)");
        if (!same_architecture(process)) {
            fprintf(stderr, "jinject: architecture does not match the game\n");
            return 1;
        }
        if (inject(process, argv[2]))
            return 1;
        printf("pid=%lu\n", pid);
        return 0;
    }

    fprintf(stderr, "usage: jinject launch <dll> <dir> <exe> <cmdline> | attach <dll> <pid>\n");
    return 2;
}

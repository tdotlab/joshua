/*
 * ffclock: injected into a game so that time spent frozen never reaches it.
 *
 * The controller suspends the whole process, and before resuming it adds the
 * suspended duration to FFShared.offset_qpc. Every hooked clock subtracts that
 * offset, so the game sees one continuous timeline.
 *
 * Hooking is done by rewriting import tables, which needs no third-party code.
 * Entries are matched by the address they currently point to, so API-set and
 * forwarded imports are covered without parsing names. Modules under the Windows
 * directory are left alone (except the C/C++ runtimes) so that system code is
 * never offset twice.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <mmsystem.h>
#include <psapi.h>
#include <stdio.h>
#include <wchar.h>

#define FF_MAGIC 0x4B434646u /* "FFCK" */
#define FF_VERSION 1u

/* Layout is mirrored in src/freezeframe/process.py. */
typedef struct {
    DWORD magic;
    DWORD version;
    volatile LONG64 offset_qpc; /* written only while this process is suspended */
    volatile LONG patched;      /* number of import entries redirected */
    volatile LONG ready;
} FFShared;

static FFShared *shared;
static HMODULE self_module;
static LONG64 qpc_frequency;
static wchar_t windows_dir[MAX_PATH];
static size_t windows_dir_len;
static CRITICAL_SECTION patch_lock;

static volatile LONG64 last_qpc;
static volatile LONG64 last_tick64;
static volatile LONG last_tgt;

static LONG64 offset_counts(void)
{
    return InterlockedCompareExchange64(&shared->offset_qpc, 0, 0);
}

static LONG64 offset_ms(void)
{
    LONG64 counts = offset_counts();
    return counts / qpc_frequency * 1000 + counts % qpc_frequency * 1000 / qpc_frequency;
}

/* Coarse tick sources can land a few milliseconds early after a resume, so
 * never hand out a value older than one already returned. */
static LONG64 monotonic64(volatile LONG64 *last, LONG64 value)
{
    for (;;) {
        LONG64 previous = InterlockedCompareExchange64(last, 0, 0);
        if (value <= previous)
            return previous;
        if (InterlockedCompareExchange64(last, value, previous) == previous)
            return value;
    }
}

/*
 * Every hook reads the real clock first and the offset second. A freeze that
 * lands between the two then yields a value that is too old, which the
 * monotonic clamp absorbs. The other order would yield a jump far into the
 * future, and the clamp would hold the clock there for the length of the freeze.
 * The reads are separate statements because C does not order operands.
 */
static BOOL WINAPI hook_QueryPerformanceCounter(LARGE_INTEGER *out)
{
    LARGE_INTEGER now;
    if (!QueryPerformanceCounter(&now))
        return FALSE;
    out->QuadPart = monotonic64(&last_qpc, now.QuadPart - offset_counts());
    return TRUE;
}

static ULONGLONG WINAPI hook_GetTickCount64(void)
{
    LONG64 real = (LONG64)GetTickCount64();
    return (ULONGLONG)monotonic64(&last_tick64, real - offset_ms());
}

static DWORD WINAPI hook_GetTickCount(void)
{
    return (DWORD)hook_GetTickCount64();
}

static DWORD WINAPI hook_timeGetTime(void)
{
    DWORD real = timeGetTime();
    DWORD value = real - (DWORD)offset_ms();
    for (;;) {
        LONG previous = last_tgt;
        if ((LONG)(value - (DWORD)previous) <= 0)
            return (DWORD)previous;
        if (InterlockedCompareExchange(&last_tgt, (LONG)value, previous) == previous)
            return value;
    }
}

static void patch_all(void);
static FARPROC WINAPI hook_GetProcAddress(HMODULE module, LPCSTR name);

static HMODULE WINAPI hook_LoadLibraryA(LPCSTR name)
{
    HMODULE module = LoadLibraryA(name);
    DWORD error = GetLastError();
    if (module)
        patch_all();
    SetLastError(error);
    return module;
}

static HMODULE WINAPI hook_LoadLibraryW(LPCWSTR name)
{
    HMODULE module = LoadLibraryW(name);
    DWORD error = GetLastError();
    if (module)
        patch_all();
    SetLastError(error);
    return module;
}

static HMODULE WINAPI hook_LoadLibraryExA(LPCSTR name, HANDLE file, DWORD flags)
{
    HMODULE module = LoadLibraryExA(name, file, flags);
    DWORD error = GetLastError();
    if (module)
        patch_all();
    SetLastError(error);
    return module;
}

static HMODULE WINAPI hook_LoadLibraryExW(LPCWSTR name, HANDLE file, DWORD flags)
{
    HMODULE module = LoadLibraryExW(name, file, flags);
    DWORD error = GetLastError();
    if (module)
        patch_all();
    SetLastError(error);
    return module;
}

#define SOURCES 3
typedef struct {
    const char *name;
    const char *dlls[SOURCES];
    const char *aliases[SOURCES]; /* NULL means the same as name */
    void *hook;
    void *real[SOURCES];
} Hook;

#define KERNEL {"kernel32.dll", "kernelbase.dll", NULL}, {NULL, NULL, NULL}

static Hook hooks[] = {
    {"QueryPerformanceCounter",
     {"kernel32.dll", "kernelbase.dll", "ntdll.dll"},
     {NULL, NULL, "RtlQueryPerformanceCounter"},
     (void *)hook_QueryPerformanceCounter},
    {"GetTickCount", KERNEL, (void *)hook_GetTickCount},
    {"GetTickCount64", KERNEL, (void *)hook_GetTickCount64},
    {"timeGetTime", {"winmm.dll", "winmmbase.dll", NULL}, {NULL, NULL, NULL}, (void *)hook_timeGetTime},
    {"GetProcAddress", KERNEL, (void *)hook_GetProcAddress},
    {"LoadLibraryA", KERNEL, (void *)hook_LoadLibraryA},
    {"LoadLibraryW", KERNEL, (void *)hook_LoadLibraryW},
    {"LoadLibraryExA", KERNEL, (void *)hook_LoadLibraryExA},
    {"LoadLibraryExW", KERNEL, (void *)hook_LoadLibraryExW},
};
#define HOOK_COUNT (sizeof(hooks) / sizeof(hooks[0]))

static void *hook_for(void *target)
{
    size_t i, j;
    for (i = 0; i < HOOK_COUNT; i++)
        for (j = 0; j < SOURCES; j++)
            if (hooks[i].real[j] && hooks[i].real[j] == target)
                return hooks[i].hook;
    return NULL;
}

/* Catches code that looks its clock up at run time instead of importing it. */
static FARPROC WINAPI hook_GetProcAddress(HMODULE module, LPCSTR name)
{
    FARPROC result = GetProcAddress(module, name);
    DWORD error = GetLastError();
    void *replacement = result ? hook_for((void *)result) : NULL;
    SetLastError(error);
    return replacement ? (FARPROC)replacement : result;
}

static int should_patch(HMODULE module)
{
    wchar_t path[MAX_PATH * 2];
    const wchar_t *base;
    DWORD length = GetModuleFileNameW(module, path, MAX_PATH * 2);

    if (module == self_module || !length || length >= MAX_PATH * 2)
        return 0;
    if (_wcsnicmp(path, windows_dir, windows_dir_len) != 0)
        return 1;
    base = wcsrchr(path, L'\\');
    base = base ? base + 1 : path;
    return _wcsnicmp(base, L"msvcp", 5) == 0 || _wcsnicmp(base, L"msvcr", 5) == 0 ||
           _wcsnicmp(base, L"vcruntime", 9) == 0 || _wcsnicmp(base, L"ucrtbase", 8) == 0;
}

static void patch_module(HMODULE module)
{
    __try {
        BYTE *base = (BYTE *)module;
        IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
        IMAGE_NT_HEADERS *nt;
        IMAGE_DATA_DIRECTORY directory;
        IMAGE_IMPORT_DESCRIPTOR *descriptor;

        if (dos->e_magic != IMAGE_DOS_SIGNATURE)
            return;
        nt = (IMAGE_NT_HEADERS *)(base + dos->e_lfanew);
        directory = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
        if (!directory.VirtualAddress || !directory.Size)
            return;

        descriptor = (IMAGE_IMPORT_DESCRIPTOR *)(base + directory.VirtualAddress);
        for (; descriptor->Name; descriptor++) {
            IMAGE_THUNK_DATA *thunk = (IMAGE_THUNK_DATA *)(base + descriptor->FirstThunk);
            for (; thunk->u1.Function; thunk++) {
                void **slot = (void **)&thunk->u1.Function;
                void *replacement = hook_for(*slot);
                DWORD old, ignored;
                if (!replacement || !VirtualProtect(slot, sizeof(void *), PAGE_READWRITE, &old))
                    continue;
                InterlockedExchangePointer(slot, replacement);
                VirtualProtect(slot, sizeof(void *), old, &ignored);
                InterlockedIncrement(&shared->patched);
            }
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        /* A malformed or vanishing module is skipped rather than crashing the game. */
    }
}

static void patch_all(void)
{
    HMODULE modules[1024];
    DWORD needed = 0, count, i;

    EnterCriticalSection(&patch_lock);
    if (EnumProcessModules(GetCurrentProcess(), modules, sizeof(modules), &needed)) {
        count = needed / sizeof(HMODULE);
        if (count > 1024)
            count = 1024;
        for (i = 0; i < count; i++)
            if (should_patch(modules[i]))
                patch_module(modules[i]);
    }
    LeaveCriticalSection(&patch_lock);
}

static int initialise(void)
{
    wchar_t name[64];
    LARGE_INTEGER frequency, now;
    HANDLE mapping;
    size_t i, j;
    UINT length;

    swprintf(name, 64, L"Local\\FreezeframeClock-%lu", GetCurrentProcessId());
    mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, sizeof(FFShared), name);
    if (!mapping)
        return 0;
    shared = (FFShared *)MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(FFShared));
    if (!shared)
        return 0;

    QueryPerformanceFrequency(&frequency);
    qpc_frequency = frequency.QuadPart;
    length = GetWindowsDirectoryW(windows_dir, MAX_PATH - 1);
    if (!length || length >= MAX_PATH - 1)
        return 0;
    windows_dir[length] = L'\\';
    windows_dir[length + 1] = L'\0';
    windows_dir_len = length + 1;
    InitializeCriticalSection(&patch_lock);

    shared->magic = FF_MAGIC;
    shared->version = FF_VERSION;
    QueryPerformanceCounter(&now);
    last_qpc = now.QuadPart - shared->offset_qpc;
    last_tick64 = (LONG64)GetTickCount64() - offset_ms();
    last_tgt = (LONG)(timeGetTime() - (DWORD)offset_ms());

    for (i = 0; i < HOOK_COUNT; i++) {
        for (j = 0; j < SOURCES; j++) {
            HMODULE source = hooks[i].dlls[j] ? GetModuleHandleA(hooks[i].dlls[j]) : NULL;
            if (source)
                hooks[i].real[j] = (void *)GetProcAddress(
                    source, hooks[i].aliases[j] ? hooks[i].aliases[j] : hooks[i].name);
        }
    }

    patch_all();
    InterlockedExchange(&shared->ready, 1);
    return 1;
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        HMODULE pinned;
        self_module = instance;
        DisableThreadLibraryCalls(instance);
        /* Patched imports point into this DLL, so it must never be unloaded. */
        GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_PIN | GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
                           (LPCWSTR)(void *)DllMain, &pinned);
        return initialise();
    }
    return TRUE;
}

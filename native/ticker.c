/*
 * ticker: a deliberately unforgiving test game.
 *
 * It runs a fixed 60 Hz simulation and catches up on every millisecond its clock
 * says has passed, with no cap, so a leaked pause shows up at once. Arrow keys
 * move a square, clicks drop a marker, and the state is written to a JSON file.
 *
 *   ticker [--clock tgt|tick|tick64|qpc] [--dynamic] [--state <file>] [--life <seconds>] [--hidden]
 *
 * --dynamic fetches the clock through GetProcAddress instead of importing it.
 * --hidden never shows the window, for tests that need only the clock.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <windowsx.h>
#include <mmsystem.h>
#include <shellapi.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

typedef DWORD(WINAPI *TickFn)(void);
typedef ULONGLONG(WINAPI *Tick64Fn)(void);
typedef BOOL(WINAPI *QpcFn)(LARGE_INTEGER *);

enum { CLOCK_TGT, CLOCK_TICK, CLOCK_TICK64, CLOCK_QPC };

static int clock_kind = CLOCK_TGT;
static int dynamic_lookup, hidden;
static const wchar_t *state_path;
static ULONGLONG life_seconds = 120;

static TickFn dyn_tgt, dyn_tick;
static Tick64Fn dyn_tick64;
static QpcFn dyn_qpc;

static int running = 1;
static double x = 320, y = 240;
static unsigned long long frames, ticks;
static double game_ms, max_dt, accumulator;
static int keydowns, last_key, clicks, click_x = -1, click_y = -1;

static double clock_ms(void)
{
    LARGE_INTEGER counter, frequency;
    switch (clock_kind) {
    case CLOCK_TICK:
        return dynamic_lookup ? dyn_tick() : GetTickCount();
    case CLOCK_TICK64:
        return (double)(dynamic_lookup ? dyn_tick64() : GetTickCount64());
    case CLOCK_QPC:
        QueryPerformanceFrequency(&frequency);
        if (dynamic_lookup)
            dyn_qpc(&counter);
        else
            QueryPerformanceCounter(&counter);
        return counter.QuadPart * 1000.0 / frequency.QuadPart;
    default:
        return dynamic_lookup ? dyn_tgt() : timeGetTime();
    }
}

/* Wall-clock time that the hook leaves alone, for the lifetime limit. */
static ULONGLONG wall_ms(void)
{
    FILETIME time;
    GetSystemTimeAsFileTime(&time);
    return (((ULONGLONG)time.dwHighDateTime << 32) | time.dwLowDateTime) / 10000;
}

static void write_state(void)
{
    wchar_t temporary[MAX_PATH];
    FILE *file;
    if (!state_path)
        return;
    swprintf(temporary, MAX_PATH, L"%ls.tmp", state_path);
    if (_wfopen_s(&file, temporary, L"w") || !file)
        return;
    fprintf(file,
            "{\"pid\": %lu, \"frames\": %llu, \"ticks\": %llu, \"game_ms\": %.1f, \"max_dt\": %.1f, "
            "\"x\": %.1f, \"y\": %.1f, \"keydowns\": %d, \"last_key\": %d, \"clicks\": %d, "
            "\"click_x\": %d, \"click_y\": %d, \"wall_ms\": %llu}\n",
            GetCurrentProcessId(), frames, ticks, game_ms, max_dt, x, y, keydowns, last_key, clicks,
            click_x, click_y, wall_ms());
    fclose(file);
    MoveFileExW(temporary, state_path, MOVEFILE_REPLACE_EXISTING);
}

static void draw(HWND window)
{
    HDC screen = GetDC(window);
    HDC memory = CreateCompatibleDC(screen);
    HBITMAP bitmap = CreateCompatibleBitmap(screen, 640, 480);
    HGDIOBJ old = SelectObject(memory, bitmap);
    HBRUSH background = CreateSolidBrush(RGB(24, 28, 40));
    HBRUSH player = CreateSolidBrush(RGB(240, 180, 40));
    HBRUSH marker = CreateSolidBrush(RGB(230, 60, 90));
    RECT all = {0, 0, 640, 480};
    RECT box = {(int)x - 15, (int)y - 15, (int)x + 15, (int)y + 15};
    char text[160];

    FillRect(memory, &all, background);
    FillRect(memory, &box, player);
    if (clicks) {
        RECT dot = {click_x - 5, click_y - 5, click_x + 5, click_y + 5};
        FillRect(memory, &dot, marker);
    }
    SetBkMode(memory, TRANSPARENT);
    SetTextColor(memory, RGB(255, 255, 255));
    snprintf(text, sizeof(text), "ticks %llu   x %.0f  y %.0f   clicks %d", ticks, x, y, clicks);
    TextOutA(memory, 10, 10, text, (int)strlen(text));
    BitBlt(screen, 0, 0, 640, 480, memory, 0, 0, SRCCOPY);

    DeleteObject(background);
    DeleteObject(player);
    DeleteObject(marker);
    SelectObject(memory, old);
    DeleteObject(bitmap);
    DeleteDC(memory);
    ReleaseDC(window, screen);
}

static LRESULT CALLBACK procedure(HWND window, UINT message, WPARAM wparam, LPARAM lparam)
{
    switch (message) {
    case WM_KEYDOWN:
        keydowns++;
        last_key = (int)wparam;
        return 0;
    case WM_LBUTTONDOWN:
        clicks++;
        click_x = GET_X_LPARAM(lparam);
        click_y = GET_Y_LPARAM(lparam);
        return 0;
    case WM_DESTROY:
        running = 0;
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

static void step_simulation(HWND window)
{
    ticks++;
    if (GetForegroundWindow() != window)
        return;
    if (GetAsyncKeyState(VK_LEFT) & 0x8000)
        x -= 2;
    if (GetAsyncKeyState(VK_RIGHT) & 0x8000)
        x += 2;
    if (GetAsyncKeyState(VK_UP) & 0x8000)
        y -= 2;
    if (GetAsyncKeyState(VK_DOWN) & 0x8000)
        y += 2;
    x = x < 15 ? 15 : x > 625 ? 625 : x;
    y = y < 15 ? 15 : y > 465 ? 465 : y;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command, int show)
{
    const double step = 1000.0 / 60.0;
    WNDCLASSW window_class = {0};
    RECT size = {0, 0, 640, 480};
    HMODULE kernel = GetModuleHandleW(L"kernel32.dll");
    ULONGLONG started, written = 0;
    wchar_t **argv;
    double last;
    HWND window;
    MSG message;
    int argc, i;

    (void)previous;
    (void)command;
    argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    for (i = 1; i < argc; i++) {
        if (wcscmp(argv[i], L"--dynamic") == 0) {
            dynamic_lookup = 1;
        } else if (wcscmp(argv[i], L"--hidden") == 0) {
            hidden = 1;
        } else if (wcscmp(argv[i], L"--state") == 0 && i + 1 < argc) {
            state_path = argv[++i];
        } else if (wcscmp(argv[i], L"--life") == 0 && i + 1 < argc) {
            life_seconds = wcstoul(argv[++i], NULL, 10);
        } else if (wcscmp(argv[i], L"--clock") == 0 && i + 1 < argc) {
            i++;
            clock_kind = wcscmp(argv[i], L"tick") == 0     ? CLOCK_TICK
                         : wcscmp(argv[i], L"tick64") == 0 ? CLOCK_TICK64
                         : wcscmp(argv[i], L"qpc") == 0    ? CLOCK_QPC
                                                           : CLOCK_TGT;
        }
    }
    if (dynamic_lookup) {
        HMODULE winmm = LoadLibraryW(L"winmm.dll");
        dyn_tgt = (TickFn)GetProcAddress(winmm, "timeGetTime");
        dyn_tick = (TickFn)GetProcAddress(kernel, "GetTickCount");
        dyn_tick64 = (Tick64Fn)GetProcAddress(kernel, "GetTickCount64");
        dyn_qpc = (QpcFn)GetProcAddress(kernel, "QueryPerformanceCounter");
    }

    SetProcessDPIAware();
    timeBeginPeriod(1);
    window_class.lpfnWndProc = procedure;
    window_class.hInstance = instance;
    window_class.hCursor = LoadCursorW(NULL, (LPCWSTR)IDC_ARROW);
    window_class.lpszClassName = L"FreezeframeTicker";
    RegisterClassW(&window_class);
    AdjustWindowRect(&size, WS_OVERLAPPEDWINDOW, FALSE);
    window = CreateWindowW(window_class.lpszClassName, L"Freezeframe Ticker", WS_OVERLAPPEDWINDOW,
                           120, 120, size.right - size.left, size.bottom - size.top, NULL, NULL,
                           instance, NULL);
    if (!hidden)
        ShowWindow(window, show);

    started = wall_ms();
    last = clock_ms();
    while (running && wall_ms() - started < life_seconds * 1000) {
        double now, dt;
        while (PeekMessageW(&message, NULL, 0, 0, PM_REMOVE)) {
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
        now = clock_ms();
        dt = now - last;
        last = now;
        if (dt > max_dt)
            max_dt = dt;
        game_ms += dt;
        accumulator += dt;
        while (accumulator >= step) {
            accumulator -= step;
            step_simulation(window);
        }
        frames++;
        draw(window);
        if (wall_ms() - written >= 20) {
            written = wall_ms();
            write_state();
        }
        Sleep(5);
    }
    timeEndPeriod(1);
    return 0;
}

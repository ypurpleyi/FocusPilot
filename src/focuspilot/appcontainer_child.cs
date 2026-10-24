// Fixed offline test program, compiled only into the disposable prototype root.
using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Threading;

class AppContainerChild {
    [DllImport("FirewallAPI.dll", CharSet=CharSet.Unicode)]
    static extern uint NetworkIsolationDiagnoseConnectFailureAndGetInfo(string server, out int error);
    [DllImport("advapi32.dll", SetLastError=true)]
    static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
    [DllImport("advapi32.dll", SetLastError=true)]
    static extern bool GetTokenInformation(IntPtr token, int kind, out int value, int size, out int needed);
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DllImport("winsqlite3.dll", CallingConvention=CallingConvention.Cdecl)]
    static extern int sqlite3_open_v2(byte[] path, out IntPtr database, int flags, IntPtr vfs);
    [DllImport("winsqlite3.dll", CallingConvention=CallingConvention.Cdecl)]
    static extern int sqlite3_exec(IntPtr database, byte[] sql, IntPtr callback, IntPtr arg, out IntPtr error);
    [DllImport("winsqlite3.dll", CallingConvention=CallingConvention.Cdecl)]
    static extern int sqlite3_close(IntPtr database);
    [DllImport("winsqlite3.dll", CallingConvention=CallingConvention.Cdecl)]
    static extern void sqlite3_free(IntPtr pointer);

    static bool IsContainer() {
        IntPtr token;
        if (!OpenProcessToken(GetCurrentProcess(), 8, out token)) throw new Exception("token-open");
        try {
            int value, needed;
            if (!GetTokenInformation(token, 29, out value, 4, out needed)) throw new Exception("token-query");
            return value == 1;
        } finally { CloseHandle(token); }
    }
    static bool ReadDenied(string path) {
        try { File.ReadAllText(path); return false; }
        catch (UnauthorizedAccessException) { return true; }
    }
    static bool WriteDenied(string path) {
        try { using (File.Open(path, FileMode.Open, FileAccess.ReadWrite)) {} return false; }
        catch (UnauthorizedAccessException) { return true; }
    }
    static byte[] Utf8(string text) { return System.Text.Encoding.UTF8.GetBytes(text + "\0"); }
    static bool SqliteWriteDenied(string path) {
        IntPtr database;
        int opened = sqlite3_open_v2(Utf8(path), out database, 2, IntPtr.Zero);
        try {
            if (opened == 14 || opened == 8) return true;
            if (opened != 0) return false;
            IntPtr error;
            int result = sqlite3_exec(database, Utf8("UPDATE tasks SET title='must-not-change'"),
                                      IntPtr.Zero, IntPtr.Zero, out error);
            if (error != IntPtr.Zero) sqlite3_free(error);
            return result == 8 || result == 14;
        } finally { if (database != IntPtr.Zero) sqlite3_close(database); }
    }
    static bool LoopbackDenied(string work, int port) {
        using (Socket socket = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp)) {
            try {
                IAsyncResult attempt = socket.BeginConnect(new IPEndPoint(IPAddress.Loopback, port), null, null);
                bool finished;
                using (var wait = attempt.AsyncWaitHandle) { finished = wait.WaitOne(800); }
                if (!finished) { File.WriteAllText(Path.Combine(work, "network.txt"), "timeout-not-proof-of-permission-denial"); return false; }
                socket.EndConnect(attempt);
                File.WriteAllText(Path.Combine(work, "network.txt"), "connected"); return false;
            }
            catch (SocketException error) {
                File.WriteAllText(Path.Combine(work, "network.txt"), error.SocketErrorCode.ToString() + ":" + error.ErrorCode);
                return error.SocketErrorCode == SocketError.AccessDenied;
            }
        }
    }
    static bool NetworkCapabilityMissing(string work) {
        int kind;
        // Capability classification only; numeric documentation address, no connection/DNS request.
        uint result = NetworkIsolationDiagnoseConnectFailureAndGetInfo("192.0.2.1", out kind);
        File.WriteAllText(Path.Combine(work, "network-capability.txt"), result + ":" + kind);
        return result == 0 && kind >= 1 && kind <= 3;
    }
    static void Put(StreamWriter writer, string key, bool value) {
        writer.WriteLine(key + "=" + (value ? "true" : "false")); writer.Flush();
    }
    static Process Spawn(string mode, string root) {
        var info = new ProcessStartInfo(Process.GetCurrentProcess().MainModule.FileName,
                                        mode + " \"" + root + "\"");
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        return Process.Start(info);
    }
    static int Main(string[] args) {
        try {
            string mode = args[0], root = args[1], work = Path.Combine(root, "work");
            if (mode == "network-control") {
                LoopbackDenied(work, int.Parse(args[2]));
                return File.ReadAllText(Path.Combine(work, "network.txt")) == "connected" ? 0 : 8;
            }
            if (mode == "sleep") {
                File.WriteAllText(Path.Combine(work, "sleep.ready"),
                                  Process.GetCurrentProcess().Id + ":" + IsContainer());
                Thread.Sleep(Timeout.Infinite); return 0;
            }
            if (mode == "leaf") {
                using (var leaf = new StreamWriter(Path.Combine(work, "leaf.txt"))) {
                    Put(leaf, "child_is_appcontainer", IsContainer());
                    Put(leaf, "child_write_denied", WriteDenied(Path.Combine(root, "readonly.txt")));
                    Put(leaf, "child_read_denied", ReadDenied(Path.Combine(root, "ungranted.txt")));
                }
                return 0;
            }
            using (var report = new StreamWriter(Path.Combine(work, "report.txt"))) {
                Put(report, "is_appcontainer", IsContainer());
                Put(report, "allowed_read", File.ReadAllText(Path.Combine(root, "readonly.txt")) == "fictional-readonly");
                Put(report, "readonly_write_denied", WriteDenied(Path.Combine(root, "readonly.txt")));
                Put(report, "ungranted_read_denied", ReadDenied(Path.Combine(root, "ungranted.txt")));
                Put(report, "ungranted_write_denied", WriteDenied(Path.Combine(root, "ungranted.txt")));
                Put(report, "sqlite_write_denied", SqliteWriteDenied(Path.Combine(root, "fixture.sqlite3")));
                Put(report, "loopback_connect_denied", LoopbackDenied(work, int.Parse(args[2])));
                Put(report, "network_capability_missing", NetworkCapabilityMissing(work));
                File.WriteAllText(Path.Combine(work, "written.txt"), "fictional-workspace");
                Put(report, "workspace_write", true);
                using (Process child = Spawn("leaf", root)) {
                    if (!child.WaitForExit(5000)) throw new Exception("leaf-timeout");
                    Put(report, "child_completed", child.ExitCode == 0);
                }
            }
            if (mode == "linger") { using (Process sleeper = Spawn("sleep", root)) { Thread.Sleep(Timeout.Infinite); } }
            return 0;
        } catch { return 9; } // No private diagnostics or unbounded output.
    }
}

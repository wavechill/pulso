using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Microsoft.Win32.SafeHandles;
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

[assembly: AssemblyTitle("Pulso")]
[assembly: AssemblyProduct("Pulso")]
[assembly: AssemblyDescription("Jogo de ritmo para Windows")]
[assembly: AssemblyVersion("1.2.0.0")]
[assembly: AssemblyFileVersion("1.2.0.0")]

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        string data = Environment.GetEnvironmentVariable("PULSO_DATA_DIR");
        if (String.IsNullOrWhiteSpace(data))
            data = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Pulso");
        data = Path.GetFullPath(data);
        string id;
        using (SHA256 hash = SHA256.Create())
            id = Convert.ToBase64String(hash.ComputeHash(Encoding.UTF8.GetBytes(data.ToUpperInvariant()))).Replace('/', '_');

        bool first;
        using (var instance = new Mutex(true, @"Local\Pulso_" + id, out first))
        {
            if (!first)
            {
                try
                {
                    using (var existing = EventWaitHandle.OpenExisting(@"Local\Pulso_focus_" + id))
                        existing.Set();
                }
                catch (WaitHandleCannotBeOpenedException) { }
                return;
            }
            using (var focus = new EventWaitHandle(false, EventResetMode.AutoReset, @"Local\Pulso_focus_" + id))
            {
                int port;
                if (!Int32.TryParse(Environment.GetEnvironmentVariable("PULSO_PORT"), out port) || port < 1024 || port > 65535)
                    port = 8765;
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new PulsoWindow(data, port, focus));
            }
        }
    }
}

internal sealed class PulsoWindow : WindowFrame
{
    private readonly Uri home;
    private readonly string data;
    private readonly Label startup;
    private readonly WebView2 view = new WebView2();
    private readonly HttpClient http = new HttpClient(new HttpClientHandler { UseProxy = false });
    private readonly RegisteredWaitHandle focusRegistration;
    private readonly object logLock = new object();
    private Process server;
    private SafeFileHandle lifetime;
    private Uri peer;

    internal PulsoWindow(string dataDirectory, int port, EventWaitHandle focus)
    {
        data = dataDirectory;
        home = new Uri("http://127.0.0.1:" + port + "/");
        Text = "Pulso";
        Icon = System.Drawing.Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(21, 22, 21);
        MinimumSize = new Size(440, 480);
        ClientSize = new Size(1180, 830);
        startup = new Label {
            Text = "Abrindo o Pulso…",
            Dock = DockStyle.Fill,
            Font = new Font("Segoe UI", 17f, FontStyle.Bold),
            ForeColor = Color.FromArgb(244, 243, 234),
            TextAlign = ContentAlignment.MiddleCenter
        };
        view.DefaultBackgroundColor = BackColor;
        view.Dock = DockStyle.Fill;
        view.Visible = false;
        Controls.Add(view);
        Controls.Add(startup);
        FloatWindowButtons();
        http.Timeout = TimeSpan.FromMilliseconds(900);
        Shown += async (sender, args) => await Launch();
        focusRegistration = ThreadPool.RegisterWaitForSingleObject(focus, (x, timedOut) =>
        {
            if (!IsHandleCreated || IsDisposed) return;
            try { BeginInvoke((Action)BringForward); }
            catch (InvalidOperationException) { }
        }, null, -1, false);
    }

    private void BringForward()
    {
        if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
        Activate();
    }

    private async Task<int> ServerStatus()
    {
        try
        {
            using (var response = await http.GetAsync(new Uri(home, "/api/state")))
            {
                if (!response.IsSuccessStatusCode) return 1;
                string result = await response.Content.ReadAsStringAsync();
                return Regex.IsMatch(result, @"""desktopProtocol""\s*:\s*1\b") &&
                    Regex.IsMatch(result, @"""isHost""\s*:\s*true") ? 2 : 1;
            }
        }
        catch (HttpRequestException) { return 0; }
        catch (TaskCanceledException) { return 0; }
    }

    private void WriteLog(string line)
    {
        if (line == null) return;
        lock (logLock)
        {
            try { File.AppendAllText(Path.Combine(data, "app.log"), line + Environment.NewLine, Encoding.UTF8); }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
    }

    private void StartServer()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string app = Path.Combine(root, "app");
        string python = Path.Combine(root, "runtime", "python", "python.exe");
        if (!File.Exists(python) || !File.Exists(Path.Combine(app, "server.py")))
            throw new InvalidOperationException("Extraia a pasta inteira do ZIP antes de abrir o Pulso.exe.");
        File.WriteAllText(Path.Combine(data, "app.log"), "", Encoding.UTF8);
        var info = new ProcessStartInfo(python) {
            WorkingDirectory = app,
            Arguments = "-B -u \"" + Path.Combine(app, "server.py") + "\" --desktop --no-browser --port " + home.Port,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };
        info.EnvironmentVariables["PULSO_DATA_DIR"] = data;
        info.EnvironmentVariables["PULSO_TOOLS_DIR"] = Path.Combine(root, "runtime", "media");
        info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        server = new Process { StartInfo = info };
        server.OutputDataReceived += (sender, args) => WriteLog(args.Data);
        server.ErrorDataReceived += (sender, args) => WriteLog(args.Data);
        server.Start();
        lifetime = ChildJob.Attach(server);
        server.BeginOutputReadLine();
        server.BeginErrorReadLine();
    }

    private async Task Launch()
    {
        try
        {
            Directory.CreateDirectory(data);
            int status = await ServerStatus();
            if (status == 1)
                throw new InvalidOperationException("A porta " + home.Port + " está em uso. Feche a versão anterior do Pulso ou o programa que usa essa porta e reabra o app.");
            if (status == 0)
            {
                if (IsDisposed) return;
                StartServer();
                for (int attempt = 0; attempt < 38 && status == 0 && !server.HasExited; attempt++)
                {
                    await Task.Delay(125);
                    if (IsDisposed) return;
                    status = await ServerStatus();
                }
                if (status != 2)
                    throw new InvalidOperationException("O servidor local do Pulso não abriu. Consulte app.log na pasta de dados do Pulso.");
            }
            if (IsDisposed) return;
            try
            {
                var environment = await CoreWebView2Environment.CreateAsync(null, Path.Combine(data, "webview"));
                await view.EnsureCoreWebView2Async(environment);
            }
            catch (WebView2RuntimeNotFoundException)
            {
                throw new InvalidOperationException("Este Windows precisa do Microsoft Edge WebView2 Runtime para abrir a janela do Pulso.");
            }
            if (IsDisposed) return;
            var core = view.CoreWebView2;
            core.Settings.IsZoomControlEnabled = false;
            core.Settings.AreDefaultContextMenusEnabled = false;
            core.Settings.IsStatusBarEnabled = false;
            try { core.Settings.IsNonClientRegionSupportEnabled = true; }
            catch (NotImplementedException) { /* Older WebView2 runtimes can still run the game. */ }
            core.NavigationStarting += (sender, args) =>
            {
                Uri url;
                if (!Uri.TryCreate(args.Uri, UriKind.Absolute, out url) ||
                    (!SameOrigin(url, home) && !SameOrigin(url, peer)))
                    args.Cancel = true;
                if (!args.Cancel && FullScreen) ToggleFullScreen(false);
            };
            core.NewWindowRequested += (sender, args) => { args.Handled = true; };
            core.WebMessageReceived += Receive;
            core.ContainsFullScreenElementChanged += (sender, args) => ToggleFullScreen(core.ContainsFullScreenElement);
            core.NavigationCompleted += (sender, args) =>
            {
                if (args.IsSuccess || args.WebErrorStatus == CoreWebView2WebErrorStatus.OperationCanceled) return;
                if (peer != null)
                {
                    peer = null;
                    core.Navigate(home.AbsoluteUri);
                    MessageBox.Show(this, "Não consegui abrir a sala. Confira a rede e se o anfitrião ainda está com o jogo aberto.", "Pulso", MessageBoxButtons.OK, MessageBoxIcon.Information);
                }
            };
            await core.AddScriptToExecuteOnDocumentCreatedAsync(
                "window.pulsoDesktop = true; document.addEventListener('DOMContentLoaded', " +
                "() => document.documentElement.classList.add('pulso-desktop'));");
            view.Visible = true;
            startup.Visible = false;
            FloatWindowButtons();
            core.Navigate(home.AbsoluteUri);
        }
        catch (Exception error)
        {
            if (IsDisposed) return;
            WriteLog(error.ToString());
            startup.Text = error is InvalidOperationException ? error.Message : "Não foi possível abrir o Pulso. Consulte app.log na pasta de dados.";
            startup.Padding = new Padding(30);
        }
    }

    private static bool SameOrigin(Uri url, Uri origin)
    {
        return origin != null && url.Scheme == origin.Scheme && url.Host == origin.Host && url.Port == origin.Port;
    }

    private static bool ReadInvite(string link, out Uri target)
    {
        if (!Uri.TryCreate(link, UriKind.Absolute, out target) || target.Scheme != "http" ||
            target.AbsolutePath != "/" || target.UserInfo.Length != 0 || target.Fragment.Length != 0 ||
            !Regex.IsMatch(target.Query, @"^\?sala=[A-Z0-9]{5}$"))
            return false;
        IPAddress ip;
        if (!IPAddress.TryParse(target.Host, out ip)) return false;
        byte[] bits = ip.GetAddressBytes();
        return bits.Length == 4 && (bits[0] == 10 || (bits[0] == 172 && bits[1] >= 16 && bits[1] <= 31) ||
            (bits[0] == 192 && bits[1] == 168) || (bits[0] == 169 && bits[1] == 254) ||
            (bits[0] == 100 && bits[1] >= 64 && bits[1] <= 127));
    }

    private void Receive(object sender, CoreWebView2WebMessageReceivedEventArgs args)
    {
        Uri origin;
        if (!Uri.TryCreate(args.Source, UriKind.Absolute, out origin)) return;
        string message;
        try { message = args.TryGetWebMessageAsString(); }
        catch (ArgumentException) { return; }
        bool allowed = SameOrigin(origin, home) || SameOrigin(origin, peer);
        if (allowed && (message == "fullscreen" || message == "exit-fullscreen"))
        {
            ToggleFullScreen(message == "fullscreen" && !FullScreen);
            view.CoreWebView2.PostWebMessageAsString(FullScreen ? "fullscreen:on" : "fullscreen:off");
            return;
        }
        if (message == "home" && SameOrigin(origin, peer))
        {
            peer = null;
            view.CoreWebView2.Navigate(home.AbsoluteUri);
        }
        else if (message.StartsWith("join:", StringComparison.Ordinal) && SameOrigin(origin, home))
        {
            Uri target;
            if (!ReadInvite(message.Substring(5), out target)) return;
            peer = target;
            view.CoreWebView2.Navigate(target.AbsoluteUri);
        }
    }

    protected override void OnFormClosed(FormClosedEventArgs e)
    {
        focusRegistration.Unregister(null);
        http.Dispose();
        if (lifetime != null) lifetime.Dispose();
        if (server != null)
        {
            try
            {
                if (!server.HasExited) server.Kill();
                server.WaitForExit(1200);
            }
            catch (InvalidOperationException) { }
            catch (System.ComponentModel.Win32Exception) { }
            server.Dispose();
        }
        base.OnFormClosed(e);
    }
}

internal static class ChildJob
{
    // Closing this handle releases Python and any media tool it started.
    internal static SafeFileHandle Attach(Process process)
    {
        SafeFileHandle job = CreateJobObject(IntPtr.Zero, null);
        if (job.IsInvalid) { job.Dispose(); return null; }
        var settings = new ExtendedLimits();
        settings.Basic.LimitFlags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (!SetInformationJobObject(job, 9, ref settings, (uint)Marshal.SizeOf(typeof(ExtendedLimits))) ||
            !AssignProcessToJobObject(job, process.Handle))
        {
            job.Dispose();
            return null;
        }
        return job;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Limits
    {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimits
    {
        public Limits Basic;
        public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemory, PeakJobMemory;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateJobObject(IntPtr attributes, string name);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(SafeFileHandle job, int kind, ref ExtendedLimits settings, uint size);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(SafeFileHandle job, IntPtr process);
}

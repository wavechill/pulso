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
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

[assembly: AssemblyTitle("Pulso")]
[assembly: AssemblyProduct("Pulso")]
[assembly: AssemblyDescription("Jogo de ritmo para Windows")]
[assembly: AssemblyVersion("1.11.1.0")]
[assembly: AssemblyFileVersion("1.11.1.0")]

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
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
                string practiceSong = args.Length == 2 && args[0] == "--treinar" &&
                    Regex.IsMatch(args[1], @"\A[a-zA-Z0-9_-]{1,96}\z") ? args[1] : null;
                Application.Run(new PulsoWindow(data, port, focus, practiceSong));
            }
        }
    }
}

internal sealed class PulsoWindow : WindowFrame
{
    private readonly Uri home;
    private readonly Uri firstPage;
    private readonly string data;
    private readonly Label startup;
    private WebView2 view;
    private readonly HttpClient http = new HttpClient(new HttpClientHandler { UseProxy = false });
    private readonly RegisteredWaitHandle focusRegistration;
    private readonly object logLock = new object();
    private Process server;
    private SafeFileHandle lifetime;
    private Uri peer;
    private GuitarFlashWindow reference;

    internal PulsoWindow(string dataDirectory, int port, EventWaitHandle focus, string practiceSong)
    {
        data = dataDirectory;
        home = new Uri("http://127.0.0.1:" + port + "/");
        firstPage = practiceSong == null ? home : new Uri(home, "?treinar=" + Uri.EscapeDataString(practiceSong));
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

    private bool PreferInstalledRuntime(string portableExe)
    {
        if (!File.Exists(portableExe)) return true;
        try
        {
            string installed = CoreWebView2Environment.GetAvailableBrowserVersionString();
            string bundled = FileVersionInfo.GetVersionInfo(portableExe).ProductVersion;
            return !String.IsNullOrEmpty(installed) &&
                CoreWebView2Environment.CompareBrowserVersions(installed, bundled) >= 0;
        }
        catch (WebView2RuntimeNotFoundException) { return false; }
        catch (Exception error) { WriteLog(error.Message); return false; }
    }

    private void AllowSandboxToReadRuntime(string folder)
    {
        // Microsoft's Fixed Version distribution requires these two read-only
        // grants for unpackaged apps on Windows 10. Limit them to the engine.
        var os = Environment.OSVersion.Version;
        if (os.Major != 10 || os.Build >= 22000) return;
        var directory = new DirectoryInfo(folder);
        if ((directory.Attributes & FileAttributes.ReparsePoint) != 0) return;
        try
        {
            var acl = directory.GetAccessControl();
            var rights = FileSystemRights.ReadAndExecute;
            var inherit = InheritanceFlags.ObjectInherit | InheritanceFlags.ContainerInherit;
            bool changed = false;
            foreach (string id in new[] { "S-1-15-2-1", "S-1-15-2-2" })
            {
                var sid = new SecurityIdentifier(id);
                bool granted = false;
                foreach (FileSystemAccessRule rule in acl.GetAccessRules(true, true, typeof(SecurityIdentifier)))
                    if (rule.IdentityReference.Equals(sid) && rule.AccessControlType == AccessControlType.Allow &&
                        (rule.FileSystemRights & rights) == rights && (rule.InheritanceFlags & inherit) == inherit &&
                        rule.PropagationFlags == PropagationFlags.None) granted = true;
                if (granted) continue;
                acl.AddAccessRule(new FileSystemAccessRule(sid, rights, inherit, PropagationFlags.None, AccessControlType.Allow));
                changed = true;
            }
            if (changed) directory.SetAccessControl(acl);
        }
        catch (UnauthorizedAccessException ex) { WriteLog(ex.Message); }
        catch (IOException ex) { WriteLog(ex.Message); }
    }

    private async Task<bool> TryRuntime(string folder)
    {
        if (IsDisposed || (folder != null && !File.Exists(Path.Combine(folder, "msedgewebview2.exe")))) return false;
        WebView2 candidate = null;
        try
        {
            if (folder != null) AllowSandboxToReadRuntime(folder);
            var environment = await CoreWebView2Environment.CreateAsync(folder, Path.Combine(data, "webview"));
            if (IsDisposed) return false;
            candidate = new WebView2 { DefaultBackgroundColor = BackColor, Dock = DockStyle.Fill, Visible = false };
            Controls.Add(candidate);
            await candidate.EnsureCoreWebView2Async(environment);
            if (IsDisposed) return false;
            view = candidate;
            WriteLog("Janela " + (folder == null ? "do sistema" : "portátil") + " · WebView2 " + environment.BrowserVersionString);
            return true;
        }
        catch (Exception error)
        {
            WriteLog("Falha ao abrir WebView2 " + (folder ?? "do sistema") + ": " + error.Message);
            return false;
        }
        finally { if (candidate != null && candidate != view) candidate.Dispose(); }
    }

    private void OfferBrowser()
    {
        startup.Visible = false;
        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 1 };
        var panel = new FlowLayoutPanel { AutoSize = true, Anchor = AnchorStyles.None, WrapContents = false,
            FlowDirection = FlowDirection.TopDown, Padding = new Padding(16) };
        var status = new Label { AutoSize = true, MaximumSize = new Size(460, 0), ForeColor = Color.WhiteSmoke,
            Font = new Font("Segoe UI", 12f), Text = "A janela do jogo não abriu. Você pode jogar no navegador deste PC. Mantenha o Pulso aberto durante a partida." };
        var address = new TextBox { ReadOnly = true, Width = 340, Text = firstPage.AbsoluteUri, Margin = new Padding(3, 12, 3, 12) };
        var play = new Button { Text = "Jogar no navegador", AutoSize = true, Padding = new Padding(8),
            BackColor = Color.Coral, FlatStyle = FlatStyle.Flat };
        play.Click += (s, e) => {
            try { Process.Start(new ProcessStartInfo(firstPage.AbsoluteUri) { UseShellExecute = true }); }
            catch (System.ComponentModel.Win32Exception) { status.Text = "Copie o endereço abaixo e abra-o no seu navegador."; address.SelectAll(); address.Focus(); }
        };
        panel.Controls.AddRange(new Control[] { status, address, play });
        layout.Controls.Add(panel);
        Controls.Add(layout);
        layout.BringToFront();
        AcceptButton = play;
        FloatWindowButtons();
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
            string portable = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "runtime", "webview2");
            bool systemFirst = PreferInstalledRuntime(Path.Combine(portable, "msedgewebview2.exe"));
            if (!await TryRuntime(systemFirst ? null : portable) && !await TryRuntime(systemFirst ? portable : null))
            {
                if (!IsDisposed) OfferBrowser();
                return;
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
            core.Navigate(firstPage.AbsoluteUri);
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
        if (allowed && message == "guitar-flash:chop-suey")
        {
            if (reference == null || reference.IsDisposed)
            {
                reference = new GuitarFlashWindow(view.CoreWebView2.Environment, Icon);
                reference.Show(this);
            }
            if (reference.WindowState == FormWindowState.Minimized) reference.WindowState = FormWindowState.Normal;
            reference.Activate();
            return;
        }
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

// The official game is a top-level document in its own WebView. Only the
// local Pulso view above receives desktop commands.
internal sealed class GuitarFlashWindow : WindowFrame
{
    private const string SongUrl = "https://guitarflash3.com/setlist/system-of-a-down/chop-suey/?lg=pt";
    private readonly WebView2 site = new WebView2();
    private readonly Label status = new Label();
    private readonly FlowLayoutPanel toolbar = new FlowLayoutPanel();

    internal GuitarFlashWindow(CoreWebView2Environment environment, Icon icon)
    {
        Text = "Chop Suey! · Guitar Flash 3";
        Icon = icon;
        BackColor = Color.FromArgb(21, 22, 21);
        ForeColor = Color.WhiteSmoke;
        Font = new Font("Segoe UI", 10f);
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(1180, 830);
        MinimumSize = new Size(580, 460);
        toolbar.Dock = DockStyle.Top;
        toolbar.Height = 68;
        toolbar.Padding = new Padding(10, 13, 0, 0);
        toolbar.WrapContents = false;
        Button back = ToolbarButton("← Voltar ao Pulso");
        back.Click += (s, e) => Close();
        Button browser = ToolbarButton("Abrir no navegador ↗");
        browser.Click += (s, e) => {
            try { Process.Start(new ProcessStartInfo(SongUrl) { UseShellExecute = true }); }
            catch (System.ComponentModel.Win32Exception) { status.Text = "O Windows não encontrou o navegador padrão."; }
        };
        toolbar.Controls.AddRange(new Control[] { back, browser });
        status.Dock = DockStyle.Fill;
        status.TextAlign = ContentAlignment.MiddleCenter;
        status.Text = "Abrindo Chop Suey! no Guitar Flash 3…";
        site.Dock = DockStyle.Fill;
        site.DefaultBackgroundColor = BackColor;
        site.Visible = false;
        Controls.Add(site);
        Controls.Add(status);
        Controls.Add(toolbar);
        FloatWindowButtons();
        Shown += async (s, e) => {
            try {
                await site.EnsureCoreWebView2Async(environment);
                if (IsDisposed) return;
                var core = site.CoreWebView2;
                core.Settings.IsWebMessageEnabled = false;
                core.Settings.AreHostObjectsAllowed = false;
                core.NewWindowRequested += (sender, args) => { args.Handled = true; };
                core.NavigationCompleted += (sender, args) => {
                    if (IsDisposed || args.WebErrorStatus == CoreWebView2WebErrorStatus.OperationCanceled) return;
                    site.Visible = args.IsSuccess;
                    status.Visible = !args.IsSuccess;
                    if (args.IsSuccess) site.Focus();
                    else status.Text = "Não foi possível abrir o Guitar Flash. Confira a conexão ou use Abrir no navegador.";
                };
                core.ContainsFullScreenElementChanged += (sender, args) => {
                    toolbar.Visible = !core.ContainsFullScreenElement;
                    ToggleFullScreen(core.ContainsFullScreenElement);
                };
                core.Navigate(SongUrl);
            } catch (Exception) {
                if (!IsDisposed) status.Text = "Use Abrir no navegador para jogar no site oficial.";
            }
        };
    }

    private Button ToolbarButton(string text)
    {
        var button = new Button { Text = text, AutoSize = true, FlatStyle = FlatStyle.Flat,
            ForeColor = ForeColor, BackColor = BackColor, Padding = new Padding(5), Cursor = Cursors.Hand };
        button.FlatAppearance.BorderSize = 0;
        return button;
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

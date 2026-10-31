// vizsup.exe — the owner's double-click app (built by build-app.ps1 with the .NET Framework
// compiler that ships with Windows; nothing downloaded). It starts the desktop app with the
// project's own Python, without a console window, exactly as `make run` / run-desktop.ps1
// do: the same code the test suite checks, so the Telegram bot, the scheduled task and every
// stored account keep working. The exe lives in the repo root (next to backend\, desktop\,
// storage\ and .env).
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("vizsup")]
[assembly: System.Reflection.AssemblyProduct("vizsup")]
[assembly: System.Reflection.AssemblyDescription("Vietsub + lồng tiếng video Douyin / Bilibili")]

static class Launcher
{
    [STAThread]
    static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string py = Path.Combine(root, @"backend\.venv\Scripts\pythonw.exe");
        if (!File.Exists(py))
        {
            MessageBox.Show("Không thấy môi trường Python của vizsup:\n" + py +
                            "\n\nĐặt vizsup.exe trong thư mục dự án (cạnh backend, desktop, storage) " +
                            "hoặc cài lại theo README → Quick start.",
                            "vizsup", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        var psi = new ProcessStartInfo(py, "-m desktop.main")
        {
            WorkingDirectory = root,
            UseShellExecute = false,
        };
        // like run-desktop.ps1: today's PATH from the registry, so a tool installed after
        // this session started (winget ffmpeg …) is found
        string machine = Environment.GetEnvironmentVariable("Path", EnvironmentVariableTarget.Machine) ?? "";
        string user = Environment.GetEnvironmentVariable("Path", EnvironmentVariableTarget.User) ?? "";
        psi.EnvironmentVariables["Path"] = machine + ";" + user;
        try
        {
            Process.Start(psi);
        }
        catch (Exception e)
        {
            MessageBox.Show("Không mở được vizsup:\n" + e.Message, "vizsup",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        return 0;
    }
}

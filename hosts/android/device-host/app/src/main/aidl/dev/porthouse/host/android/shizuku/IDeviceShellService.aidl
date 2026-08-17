// Fixed-op shell executor running at Shizuku's shell (uid 2000) privilege.
// There is deliberately no free-form command method: every caller-visible
// operation is a fixed argv produced by OpSpec.
package dev.porthouse.host.android.shizuku;

interface IDeviceShellService {
    // Run one fixed argv template and return stdout. Non-zero exit raises a
    // remote exception carrying the exit code and truncated stderr.
    String exec(in String[] argv);

    // Capture the screen as PNG bytes (screencap -p).
    byte[] screenshot();
}

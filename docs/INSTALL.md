# Installing G-reality

## No install: the website

Open the public website in Chrome, Edge or Firefox (WebGL2, a discrete or recent integrated GPU) and press
**Play the sample world**. Click to look; WASD move, Shift sprint, Space jump, Esc release the mouse. The sample is
read-only: generating worlds and making changes needs the Windows companion.

## Windows companion

**You need:** Windows 10/11, internet on first launch, and a PostgreSQL connection URL. A free
[Neon](https://neon.tech) project works: *New project → Connect → copy the connection string*
(`postgresql://…`). A [Gemini API key](https://aistudio.google.com/apikey) is optional; without one the built-in
keyword interpreter designs worlds (they follow the description less closely).

1. Download `G-reality-win64.zip` and **unzip it** (do not run from inside the ZIP).
2. Double-click `G-reality.cmd`.
   - If Python 3.12+ is missing, the launcher offers to install the official python.org build with `winget`
     (per user, no administrator rights). Answer **Y**.
   - First launch only: paste the database URL and, optionally, the Gemini key (input is hidden). They are saved to
     `%APPDATA%\G-reality\.env.local` and never leave your PC except to reach Neon and Gemini.
   - The launcher installs runtime packages once (about a minute), creates the database tables, starts the server
     on `127.0.0.1:8765` and opens your browser.
3. Type a description and press **Generate world**, or reopen one of **Your worlds**.

Stop with Ctrl+C or by closing the launcher window. If SmartScreen warns about `G-reality.cmd`, choose
*More info → Run anyway*.

**Troubleshooting:** `G-reality.cmd -PreflightOnly` checks requirements; `G-reality.cmd -Port 8770` uses another
port; edit or delete `%APPDATA%\G-reality\.env.local` to change settings; logs are in
`%LOCALAPPDATA%\G-reality\logs`.

The experimental Gaussian far field needs WSL2 and a CUDA PyTorch environment; it is never required.

const path = require("path");
const home = process.env.USERPROFILE || process.env.HOME;

module.exports = {
  apps: [
    {
      name: "apuestas-daemon",
      script: path.join(home, ".local", "bin", "uv.exe"),
      args: "run python main.py",
      cwd: path.join(home, "apuestas-daemon"),
      interpreter: "none",
      env: {
        PATH: `${path.join(home, ".local", "bin")};${process.env.PATH}`,
      },
      restart_delay: 5000,
      max_restarts: 10,
      autorestart: true,
      watch: false,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      out_file: path.join(home, "apuestas-daemon", "logs", "out.log"),
      error_file: path.join(home, "apuestas-daemon", "logs", "error.log"),
    },
  ],
};

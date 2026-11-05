"""Copy the variables in .env to the Railway service, with FMCSA_MOCK forced to false.

Values are passed straight to the Railway CLI and never printed.
Usage: python3 scripts/railway_env.py <service>
"""

import subprocess
import sys

service = sys.argv[1]

env = {}
with open(".env") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")

env["FMCSA_MOCK"] = "false"  # the deployed app always uses the real FMCSA API

args = ["railway", "variables", "--service", service, "--skip-deploys"]
for key, value in env.items():
    args += ["--set", f"{key}={value}"]

result = subprocess.run(args, capture_output=True, text=True)
if result.returncode != 0:
    sys.exit("Failed to set Railway variables (output hidden because it may contain secrets).")
print("Set variables on Railway:", ", ".join(sorted(env)))

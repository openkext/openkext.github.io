# Report a test

Test reports are what give a project its status. A project becomes community-verified when three people who don't maintain it report its newest release working or partly working, and reported-broken when at least two of them report it broken and outnumber the working reports. Your report counts the same as anyone else's.

## Steps

1. Find the project in the [catalog](site:catalog/). At the bottom of its page there is a template filled in with its id and newest release.
2. Fork [openkext/registry](https://github.com/openkext/registry) and add `kexts/<id>/reports/<your-github-login>/<name>.toml`. The folder must be your own login. The file name is up to you, for example `1.0.0-25g332.toml`.
3. Fill in the result, your macOS version and build (`sw_vers`), and your hardware. List the features you tried under `[features]`.
4. Open a pull request with that one file. The bot checks it and merges it if it passes.

## Faster: let the script fill it in

On the Mac you tested, from a clone of the registry:

```sh
python3 tools/make_report.py <id> <version> works --device 8086:2725 \
    --feature wifi=works --notes "what you tested" --login <your-github-login> --write
```

It fills in your macOS version and build, CPU and SMBIOS model (nothing else: no serial numbers or MAC addresses), saves the file in your folder and checks it. An AI coding agent can run it for you; [AGENTS.md](https://github.com/openkext/registry/blob/main/AGENTS.md) tells it how. Read the file before you send it.

## What makes a useful report

- Say what you tested and what you didn't. "Wi-Fi works, sleep untested" is more useful than "works".
- Use `partial` when the main function works but something is missing or unreliable, and explain it in `notes`.
- Use `broken` when the driver doesn't load, panics, or its main function doesn't work. Include the macOS build and, if you can, the log line or panic message.
- Your newest report for a release is the one that counts. To change your result, add a new file; don't edit the old one.

Reports are public and tied to your GitHub account. Don't include serial numbers, MAC addresses, network names or anything else personal. Reports that are fake or part of a coordinated campaign can be removed by a maintainer, with a note in the pull request that removes them.

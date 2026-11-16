# Submit a project

Anyone who maintains an open-source kext, patch, porting tool or driver documentation for macOS on non-Apple hardware can list it. You add one file to the [registry](https://github.com/openkext/registry) with a pull request, and a bot checks it against fixed rules. If every rule passes, it merges without waiting for a person.

## Before you start

- The project is open source under a license on the [automatic list](governance.md#licenses). Other OSI-approved licenses are fine after a maintainer looks at them.
- The source is a public GitHub repository.
- There is at least one published GitHub release, and you know the SHA-256 of each asset you want to list (`shasum -a 256 FILE`).
- Your GitHub account is at least 30 days old.
- The project ships no Apple software and doesn't help anyone get around activation or licensing. See the [legal boundaries](driver-development.md#legal-boundaries) in the driver guide.

## Steps

1. Fork [openkext/registry](https://github.com/openkext/registry).
2. Create `kexts/<id>/kext.toml`, where `<id>` is a short name in lowercase letters, digits and dashes. Copy the example below and fill it in. Put your own GitHub login in `maintainers`.
3. Run the checks locally if you like: `python3 tools/check_registry.py` (Python 3.11 or newer, no dependencies).
4. Optional: add your own test report in the same pull request. See [Report a test](report.md).
5. Open the pull request. The bot answers within a couple of minutes, either with an approval, with the rule that failed, or with a note that a maintainer will review it.

Write `ai_disclosure` honestly. "none" is fine if you didn't use AI tools; otherwise name the tools and models. It is shown on the project page so users can judge for themselves.

## Updating your entry

To add a release or change the description, edit `kexts/<id>/kext.toml` in a new pull request. Published releases can't be edited or removed by the bot; add a new release instead. Changing `id`, `source`, `license` or `maintainers` goes to a maintainer. The full list is in the [governance rules](governance.md).

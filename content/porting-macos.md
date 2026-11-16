# Porting a driver to a new macOS version

What to do when a new macOS release, or a new minor build, breaks a driver that talks to private Apple interfaces. The process comes from porting the AX210 project's AWDL code from Tahoe back to Sequoia, but it applies to any kext that subclasses or calls Apple classes without public headers.

Everything that reads Apple software happens **on your own Mac, on your own licensed copy of macOS**. Don't commit kernel collections, extracted kexts, daemons, disassembly or raw symbol dumps. Commit only the declarations your driver needs (method names, slot order, struct sizes) and a short note of how you checked them. The [legal boundaries](driver-development.md#legal-boundaries) apply throughout.

## 0. Know what can break

Your own logic is portable. What depends on each macOS build is the boundary with Apple's code:

| What | How it breaks |
|---|---|
| Vtable layout of the Apple classes you subclass or call | A slot added or removed shifts every later method: the framework calls the wrong function and the machine panics |
| Classes that gained or lost virtual methods | A method that was a vtable call on one version has to be called by symbol on another |
| Structs and payloads copied by raw offset | Wrong fields: data dropped, misparsed, or a size check fails |
| Capability bits or properties a daemon checks | The feature is never enabled, with no error |
| Imported symbols | The kext doesn't load ("symbol not found") |
| Byte patterns for in-memory patches of user-space binaries | The patch doesn't apply, or worse, applies in the wrong place |

The AX210 project's [Sequoia port analysis](https://github.com/kevineduardo/ax210-airdrop-awdl-hackintosh/blob/main/docs/research/awdl-sequoia-port-analysis.md) is a full worked example of this table for Wi-Fi.

## 1. Check the imports

Build the kext, then compare its undefined symbols with what the target kernel collection exports. On macOS 11 and later the kernel and its extensions live in `/System/Library/KernelCollections/`. A script that lists the collection's exports and diffs them against `nm -u` of your kext answers the question in seconds; the AX210 project's [kcsyms.py](https://github.com/kevineduardo/ax210-airdrop-awdl-hackintosh/blob/main/tools/kc/kcsyms.py) and [check-kext.sh](https://github.com/kevineduardo/ax210-airdrop-awdl-hackintosh/blob/main/tools/check-kext.sh) do it.

Any missing symbol means the new build no longer exports it, or doesn't export it yet. That is also how you find the oldest minor version a build can support: in the AX210 project, a 15.0.1 kernel collection lacks 14 symbols the Sequoia build needs, so that build requires 15.2 or later.

The result only means something for the exact macOS build you checked.

## 2. Compare the vtables

Dump the vtable of every Apple class you subclass or call through a vtable, on each version you support, and compare the order with your headers. Some rules:

- The ancestors' slots (OSObject, IOService and so on down to your parent class) must match exactly. A difference there means the parent headers are wrong too.
- A class's own methods come right after its parent's last slot, in declaration order. Reserved slots count.
- `___cxa_pure_virtual` slots are the methods your subclass must implement.
- A slot that exists on one version and not another must be removed from that version's header, not left as a placeholder.
- Don't assume minor releases of one major version share a layout. In the AX210 project, one class's vtable differs between 15.0.1 and 15.8.1.

## 3. Write or adjust the headers

- Put each layout in its own directory (for example `include/macOS15/`, `include/macOS26/`) and select it at build time.
- Declare every slot in order. Add `static_assert(sizeof(Class) == ...)` for each instance size you measured.
- At the top of each header, write which build you checked and how, in one or two lines. No raw dumps.
- If a method changed its signature or return type between builds, note it next to the declaration.

## 4. Check data layouts that aren't in headers

Some payloads are copied by raw offset. To learn their layout, read how Apple's own driver for the same framework fills them, in a disassembler, on your local kernel collection: [ipsw](https://github.com/blacktop/ipsw) (`ipsw kernel extract` to get one kext out, `ipsw macho disass`), Ghidra, Hopper or IDA. Write down only the conclusion (offset, width, meaning), as in the AX210 project's [port changes](https://github.com/kevineduardo/ax210-airdrop-awdl-hackintosh/blob/main/docs/research/awdl-sequoia-port-changes.md).

## 5. Bring it up in stages

Add a build guard: code that checks the running kernel version (`uname -v`) and a few vtable anchor slots at load time, and keeps the risky feature off on a build that isn't on its verified list. Provide a boot-arg to override it for testing.

Never test a new ABI with everything on. Enable one stage per reboot, keep a known-good EFI on a USB stick, and boot with `keepsyms=1 debug=0x100`:

1. Create the interface or attach to the framework, but hand it nothing. The system must stay stable.
2. Turn on internal state and events, and watch the log.
3. Turn on receive, and check the counters rise.
4. Hand received data to the framework.
5. Turn on transmit, then data, then any timing optimizations.

When a stage panics, symbolicate with the dSYM from the exact build (`atos -o Driver.kext.dSYM/... -l <load address> <pc>`). Once everything works, add the new build to the verified list and to its unit test.

## 6. User-space patches

If your project patches a user-space binary at load time (as Lilu plugins do), check on each new build that every pattern still matches exactly once. Keep the patterns as small as possible, with masks for bytes that move between builds, and keep a table per binary UUID. A script that verifies the patterns against the local binary, and a unit test that checks the tables are consistent, catch most breakage before it reaches a user. The AX210 project's [BLE advertising analysis](https://github.com/kevineduardo/ax210-airdrop-awdl-hackintosh/blob/main/docs/research/ble-advsets-analysis.md) shows how to find the same code again in a new build by its log strings.

## 7. Before you publish

- Tests pass, CI builds every target, and struct sizes are consistent across compile units.
- The release notes say which macOS builds you tested, on which hardware, with which boot-args, and what worked.
- No Apple binaries or raw dumps are in the diff.
- Add the new release to your [catalog entry](submit.md) and ask people on that macOS version to [report tests](report.md).

---

Adapted from [Porting AWDL to a new macOS version](https://github.com/kevineduardo/ax210-airdrop-awdl-hackintosh/blob/main/docs/porting-awdl-to-new-macos-versions.md) in the AX210 AirDrop/AWDL project, by its contributors, MIT license.

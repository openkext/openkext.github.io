# Driver development guide

Practical notes for writing or porting macOS kernel extensions (kexts) for non-Apple hardware. Most of it applies to any kext; the examples come from network drivers, because that is where these notes started.

The worked example throughout is the [AX210 AirDrop/AWDL project](https://github.com/kevineduardo/ax210-airdrop-awdl-hackintosh), a fork of OpenIntelWireless AirportItlwm that adds AWDL and AirDrop to the Intel AX210. Its tools and research notes are linked where they show a technique in practice.

## Legal boundaries

Read this part first. These rules keep a project safe to fork and redistribute, and the [registry](governance.md#what-gets-listed) only lists projects that follow them.

- **Ship no Apple code.** No kexts, kernel collections, daemons, firmware, frameworks, or headers copied from Apple's SDKs or Kernel Debug Kits. Build against an open SDK such as [MacKernelSDK](https://github.com/acidanthera/MacKernelSDK), fetched at build time.
- **Reverse engineer only your own licensed copy of macOS, and only for interoperability:** to make your driver work with Apple's frameworks. Run the tools on your own machine and keep their raw output (symbol dumps, disassembly, decompiled code) there.
- **Commit only the minimum that interoperability needs:** class and method names, the order of virtual method slots, struct sizes and field offsets your driver has to use, and a short note of how you checked them. Write it in your own words and code. Never paste decompiled Apple code.
- **Keep byte patterns minimal.** If you patch Apple code in memory (as Lilu plugins do), commit only the shortest pattern that finds the location, checked on your own copy. Never commit the binary or large excerpts of it.
- **Respect the licenses you build on.** If you start from GPL code (itlwm, many Linux drivers), changed files carry a notice, the derived kext stays under the same license, and you publish the source with every binary. Check the license of any firmware you bundle and include it.
- **Don't help anyone get around Apple's license or activation.** Apple's macOS license only allows installing macOS on Apple-branded hardware. Guides here don't explain how to obtain macOS or how to bypass activation, and the registry doesn't list projects that do.

When a step can only be done with Apple software, document the steps for each person to run on their own Mac instead of publishing the result.

## Toolbox

| Tool | Use |
|---|---|
| [MacKernelSDK](https://github.com/acidanthera/MacKernelSDK) | Kernel headers and libraries for building kexts with a normal Xcode |
| [Lilu](https://github.com/acidanthera/Lilu) | Patching kexts and user-space binaries at load time |
| [ipsw](https://github.com/blacktop/ipsw) | Extract kexts from a kernel collection, disassemble, dump class information |
| Ghidra, Hopper or IDA | Disassembly, when you need to know how Apple's own driver fills a structure |
| `ioreg -l -w0 -r -c <Class>` | Your driver's properties in the IORegistry |
| `log stream --predicate 'process == "kernel"'` | Live kernel logs; add `AND eventMessage CONTAINS[c] "yourdriver"` to filter |
| `kmutil showloaded`, `kextstat` | What is loaded, and which build (UUID) |
| `atos` with the dSYM | Turn a panic backtrace into function names and lines (boot with `keepsyms=1`) |

The AX210 project has two small tools worth copying: [kcsyms.py](https://github.com/kevineduardo/ax210-airdrop-awdl-hackintosh/blob/main/tools/kc/kcsyms.py) lists kexts and symbols in your kernel collection and decodes C++ vtables, and [check-kext.sh](https://github.com/kevineduardo/ax210-airdrop-awdl-hackintosh/blob/main/tools/check-kext.sh) checks after every build that each struct has one layout across all compile units and that every imported symbol exists in the target kernel.

## Reading C++ classes out of a kernel collection

IOKit is C++ with the Itanium ABI. When your driver subclasses or calls an Apple class whose header isn't public, you need its virtual method order.

A vtable symbol `__ZTV<len><Class>` points to two words (offset-to-top and RTTI) followed by the method pointers, so the address point is at +16. In a kernel collection those pointers are chained fixups: the low 30 bits are the target's offset from the collection base. A tool that decodes this and names each slot from the symbol table gives you output like:

```sh
kcsyms.py vtable IO80211BufferPool
0x0118  0xffffff8002204cc6  __ZN17IO80211BufferPool9getBufferEv
```

From that you can write a header where each `virtual` method sits in the right slot. Things to remember:

- Inherited slots come first, in the parent's order. A class's own methods follow in declaration order, including `OSMetaClassDeclareReservedUnused` slots.
- A class with no virtual methods of its own ends right after its parent's slots. Calling a "virtual" method past the end reads garbage and panics; call the exported function by name instead.
- Pure virtual slots (`___cxa_pure_virtual`) are the ones a subclass must implement.
- Check every minor release you support. Layouts can change within a major version.
- Add `static_assert(sizeof(Class) == ...)` for every instance size you measured, and note at the top of the header which macOS build you checked.

## Keep the build honest

Two lessons from the AX210 project that each cost boot panics:

1. **Stale objects.** A project that includes its own headers with `-isystem` loses dependency tracking for them, because clang's `-MMD` skips system headers. Changing one struct recompiled 3 files and left 38 with the old layout, linked into one kext. Fixes: build with `-Xclang -sys-header-deps`, add a layout fingerprint symbol so objects built with another layout fail to link, and compare struct sizes in the DWARF of every compile unit after each build. The [write-up](https://github.com/kevineduardo/ax210-airdrop-awdl-hackintosh/blob/main/docs/research/panic-stale-struct-layout.md) has the details.
2. **Debug and Release are different binaries,** and a bug can hide in either one. Test the configuration you ship.

Also build in CI from a clean checkout, and attach the dSYM to each release so users can symbolicate panics.

## Make the driver observable

Publish every counter that might help debugging as an IORegistry property from day one. It is cheap, and it turns "it sometimes fails" into numbers you can compare. In the AX210 project, counters for TX results, timing histograms and peer state are what found most of the AirDrop bugs, for example "45 % of off-channel dwells start more than 10 ms late".

Put risky features behind boot-args, default off, so a user can always get back to a working system by removing a flag. Add run-time switches (through `setProperties`, administrator only) for anything you want to compare within one boot.

## Testing on hardware

- Make one change per boot when bringing something up.
- Record before and after. A script that writes a timeline per attempt pays for itself quickly.
- Compare a success with a failure from the same build before changing code.
- Keep a known-good EFI on a USB stick, and write down every EFI you boot along with the kext UUID.
- Always boot test builds with `keepsyms=1 debug=0x100`, so a panic report names the function.

## Publishing your driver

- Pick an open-source license compatible with the code you started from, and keep upstream notices.
- Publish binaries only as GitHub releases built from tagged source, and give their SHA-256 in the release notes.
- Disclose AI use in the README and in your registry entry. Users want to know, and it costs nothing.
- List it in the catalog: see [Submit a project](submit.md). Ask people to [report tests](report.md), including failures.

## Where to learn more

- Apple's [IOKit Fundamentals](https://developer.apple.com/library/archive/documentation/DeviceDrivers/Conceptual/IOKitFundamentals/) and [Kernel Programming Guide](https://developer.apple.com/library/archive/documentation/Darwin/Conceptual/KernelProgramming/). Old, but still the best overview.
- The [XNU sources](https://github.com/apple-oss-distributions/xnu) for the kernel side (APSL).
- The [Dortania guides](https://dortania.github.io/) for how OpenCore injects kexts.
- [itlwm](https://github.com/OpenIntelWireless/itlwm), a complete example of porting a BSD Wi-Fi driver to macOS.
- [Porting a driver to new hardware](porting-hardware.md) and [Porting a driver to a new macOS version](porting-macos.md) in these docs.

---

Adapted from [macOS driver development notes](https://github.com/kevineduardo/ax210-airdrop-awdl-hackintosh/blob/main/docs/macos-driver-development.md) in the AX210 AirDrop/AWDL project, by its contributors, MIT license.

# Porting a driver to new hardware

A plan for bringing a device that has no macOS driver to macOS, by porting an existing open-source driver from Linux or a BSD. It is written around network chips, where most hackintosh porting happens, but the stages apply to audio, input, storage and other devices too.

Expect a large job. The Intel Wi-Fi work this guide comes from stands on years of OpenIntelWireless effort: a complete 802.11 stack port and a working driver for the chip before any macOS-specific feature was attempted. For a device with no macOS driver at all, getting the basics working is a project in itself.

Read the [legal boundaries](driver-development.md#legal-boundaries) before you start. They decide what you may look at, what you may commit, and which source drivers you may build on.

## The layers

Most ported drivers end up with the same shape:

```
macOS family (IOKit)  <->  glue  <->  protocol stack  <->  hardware layer  <->  firmware
```

- The **macOS family** is Apple's framework for the device class (IO80211Family, IOAudioFamily, IOHIDFamily, IONetworkingFamily). You subclass its classes; you don't change them.
- The **glue** translates between that family and the rest of your driver.
- The **protocol stack** is often a port of a BSD stack (net80211 for Wi-Fi in itlwm).
- The **hardware layer** is the only part that is specific to one chip: registers, DMA rings, interrupts, firmware commands.

If a driver for a related chip already exists for macOS, you may only need a new hardware layer. That is how itlwm supports several Intel generations with one stack.

## Step 1. Pick a source driver

Look for an existing open-source driver and check its license before reading any code.

| Source | Typical licenses | Notes |
|---|---|---|
| OpenBSD, FreeBSD, NetBSD | ISC, BSD-2/3-Clause | Closest to macOS in structure; itlwm's Wi-Fi stack is OpenBSD net80211, so a BSD Wi-Fi driver needs much less translation |
| Linux | GPL-2.0, sometimes dual GPL-2.0 OR BSD-3-Clause, ISC for some vendors | Widest hardware support and newest features; GPL code makes your kext GPL too |
| Vendor reference code | Varies | Read the terms; some forbid redistribution or other platforms |

Some examples for Wi-Fi: Linux `rtw89` for recent Realtek chips (GPL-2.0 OR BSD-3-Clause), OpenBSD `rtwn(4)` for older Realtek chips (ISC, and already speaks net80211), Linux `mt76` for MediaTek MT7921/MT7922 (mostly ISC), Linux `ath10k` and OpenBSD `qwx(4)` for Qualcomm Atheros.

Check the firmware too. Firmware files in `linux-firmware` come with their own redistribution terms. Include each firmware license file next to the firmware you bundle.

## Step 2. Basic function first

Get the device doing its main job before anything macOS-specific:

1. Fork the closest existing macOS driver, or start from a skeleton built on [MacKernelSDK](https://github.com/acidanthera/MacKernelSDK).
2. Implement the hardware layer: probe and attach, firmware loading, interrupts, DMA rings, power management. If the project already has a hardware-layer interface (itlwm's `ItlHalService` is one), implement that, and read an existing implementation top to bottom first.
3. Add the device IDs to the `Info.plist` personalities.
4. Start with the simplest macOS interface that exercises the hardware. For Wi-Fi that means the Ethernet-style `itlwm` target before the native `AirportItlwm` one.
5. Pass the usual tests for the device class. For Wi-Fi: scan, WPA2 and WPA3 association, sleep and wake, a day of traffic.

Expect weeks here: DMA, interrupts, power states, rate control and crypto offload all take time. Use boot-args to enable features one at a time, boot with `keepsyms=1 debug=0x100`, and check struct layouts after every build (see [Keep the build honest](driver-development.md#keep-the-build-honest)).

## Step 3. Check what the hardware can do before adding features

Before you implement a macOS feature on top, list what it needs from the hardware and check each need in the source driver and its firmware interface. That saves months spent on a feature the chip can't support.

AWDL (the link behind AirDrop) is a good example. It needs:

| Need | Why | Where to look in a Linux Wi-Fi driver |
|---|---|---|
| Off-channel dwells while associated, with a precise start notification | AWDL slots move between channels while the normal link stays up | `remain_on_channel` ops, firmware events for dwell start and end |
| A second MAC address the chip acknowledges | Peers send unicast to the AWDL interface's own address | P2P-device or multi-interface support |
| Receiving frames for a foreign BSSID | AWDL frames use their own BSSID | Per-interface RX filter control |
| Raw management and data frame TX on a chosen channel, with TX status | Discovery frames and AirDrop data | `mgmt_tx`, per-frame TX descriptors |
| Observable TX timing and a reliable clock | Frames must leave inside the peer's slot | TSF reads, dwell state, precise RX timestamps |

If the chip has P2P-device, remain-on-channel and management TX in Linux, it can almost certainly do AWDL with the AX210 project's design. The [Wi-Fi porting plan](https://github.com/kevineduardo/ax210-airdrop-awdl-hackintosh/blob/main/docs/porting-to-other-wifi-chips.md) in that project maps each callback to the Intel implementation and to the equivalent in Realtek `rtw89`.

## Step 4. Bring features up in stages

- Start receive-only. When the device sees what it should see, add transmit, then data.
- Put each stage behind its own boot-arg, default off.
- Fill counters from the start and publish them in the IORegistry. They are how you will find every problem later.

## Step 5. Measure, then tune

Measurements are what made AirDrop work on the AX210. Its firmware started off-channel dwells 10 to 75 ms late half of the time, which only showed up in a histogram, and led to the fix. Your hardware will have its own quirks. Record a session, compare a success with a failure, and fix the biggest difference first. The project's [test results](https://github.com/kevineduardo/ax210-airdrop-awdl-hackintosh/blob/main/docs/test-results.md) show what that process looks like.

## Companion devices

Some features need more than one device. AirDrop discovery also needs Bluetooth LE advertising sets: a combo card's Bluetooth half has to be a controller macOS accepts (often after a firmware-loader kext), and it needs LE extended advertising (Bluetooth 5.0 or newer). Check the whole chain before you start.

## Share what you have

If you get anywhere, even only to "the device probes", publish it and open a discussion with the upstream project. Partial ports are how the next person starts. When you have a release, [submit it to the catalog](submit.md) and ask people to [report tests](report.md).

---

Adapted from [Porting native Wi-Fi and AWDL to other Wi-Fi chips](https://github.com/kevineduardo/ax210-airdrop-awdl-hackintosh/blob/main/docs/porting-to-other-wifi-chips.md) in the AX210 AirDrop/AWDL project, by its contributors, MIT license.

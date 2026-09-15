# Compute hardware and network measurements

Measured on 2026-09-15. These are observations from the hosts and short network tests, not a distributed-GPU performance benchmark.

## Hosts

| Address | Access | GPU | Verification |
|---|---|---|---|
| `100.64.0.1` | `software@100.64.0.1` via SSH | RTX 4060, 8,188 MiB VRAM | Reported by `nvidia-smi`; this host does **not** currently report the expected RTX 3090 |
| `100.64.0.7` | `software@100.64.0.7` via SSH | RTX 4090 Laptop GPU, 16,376 MiB VRAM | Reported by `nvidia-smi`; intended primary compute host |
| `100.64.0.9` | No SSH access, confirmed by owner | RTX 4090 Laptop GPU, 16 GB VRAM | Owner-reported only; hardware and WSL environment not inspected in these tests |

Do not budget for a 24 GB RTX 3090 until its actual host is identified and verified. The previously discussed 56 GB aggregate VRAM configuration was hypothetical. Even with those GPUs, VRAM would remain physically separate; Ethernet does not automatically provide unified GPU memory.

## Network topology and links

| Host | Observed Ethernet/LAN address | Interface | Negotiated link |
|---|---|---|---|
| `.1` | `172.16.100.153/24` | `enp6s0` | 1,000 Mb/s, full duplex |
| `.7` | `172.16.100.101/24` | `enp114s0` | 1,000 Mb/s, full duplex |
| `.9` | Tailscale direct endpoint `172.16.100.193:41641` | Not inspected | Not verified |

The `.1` and `.7` hosts prefer their Ethernet routes and also have Wi-Fi connections. Their Ethernet gateway is `172.16.100.1`. LAN addresses may change with DHCP.

- `.1` advertises adapter support up to 2.5 Gb/s, but its link partner advertises only 1 Gb/s.
- `.7`'s current adapter advertises a maximum of 1 Gb/s; its link partner also advertises 1 Gb/s.
- No LLDP neighbor information was reported on `.1`. The switch make/model and wider switching capacity are **unknown**. Link negotiation establishes the immediate link limit, not the identity of the switch.
- Tailscale reported direct LAN endpoints for the tested peers rather than a relay.

## Measurements

| Test | Result |
|---|---|
| `.1 → .7` direct LAN ping, 20 packets | Average 0.728 ms; min 0.252, max 1.095 ms; 0% loss |
| `.1 → .7` Tailscale ping, 20 packets | Average 1.253 ms; min 0.862, max 1.670 ms; 0% loss |
| `.1 → .9` Tailscale ping, 20 packets | Average 1.099 ms; min 0.691, max 2.714 ms; 0% loss |
| `.7 → .9` Tailscale ping, initial 20 packets | Average 5.983 ms, including an 89.651 ms initial spike; 0% loss |
| `.7 → .9` Tailscale ping, warmed repeat of 30 packets | Average 2.783 ms; min 1.189, max 4.387 ms; 0% loss |
| `.7 → .1` direct LAN TCP | 941.4 Mb/s |
| `.1 → .7` direct LAN TCP | 941.4 Mb/s |
| `.7 → .1` Tailscale TCP | 835.1 Mb/s |
| `.1 → .7` Tailscale TCP | 887.9 Mb/s |

Ordinary LAN ping to `.9` received no response from either accessible host, while Tailscale ping succeeded. This may reflect firewall settings; it does not establish that the LAN path is broken. Throughput involving `.9` was not measured because no benchmark endpoint was available there.

### Method and limits

- ICMP tests used 100 ms intervals and the packet counts above.
- Throughput used temporary Python TCP sender/receiver processes, one connection per test, 1 MiB application buffers, and approximately five seconds of sending. Each direction and route was tested separately, not simultaneously.
- SSH launched the processes; measured payload traveled directly over the selected LAN or Tailscale TCP connection, not through SSH.
- No `iperf3`, NCCL, GPU-to-GPU transfer, distributed training, or model-sharding benchmark was run. These short tests do not characterize sustained congestion, concurrent workloads, or tail latency.
- No packages or persistent configuration were changed by these network tests. Temporary listeners on TCP port 45239 exited, and their absence was checked on both hosts.

## Compute recommendation

Use independent GPU workers with a shared job queue: assign complete clips or camera groups to a host and exchange compressed video, tracks, timestamps, and event metadata. Keep most model computation local to each GPU.

The measured gigabit link is suitable for this architecture. At 941.4 Mb/s, transferring 100 MB alone takes approximately 0.85 seconds; low ping does not remove that serialization cost.

Treat splitting one model across machines or frequently synchronizing training gradients as a separate experiment. Neither throughput nor latency measured here establishes acceptable distributed-model performance. Pipeline sharding may be worth testing for a model that cannot fit on one GPU; tightly synchronized tensor parallelism has stronger interconnect requirements. See [vLLM parallelism guidance](https://github.com/vllm-project/vllm/blob/main/docs/serving/parallelism_scaling.md).

Before committing to pooled-model compute:

1. Identify and verify the proposed RTX 3090 host.
2. Measure `.9` throughput when a benchmark endpoint can be started there.
3. Benchmark the intended model and framework against independent single-GPU workers, including latency, throughput, VRAM, and failure recovery.
4. If communication is the bottleneck, inspect the physical switch and adapters before choosing upgrades; `.7`'s current gigabit adapter would remain a limit even with a faster switch.

These notes do not authorize changes to other hosts or replace the main thread's deployment documentation.

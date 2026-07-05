# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each release corresponds to an article in the [Building with Talos](https://lelopez.io/blog/homelab-v2-00-talos-kubernetes-homelab-series) (V2) or [Security Hardening](https://lelopez.io/blog/homelab-v3-00-security-hardening-series) (V3) series, or a standalone guide. Articles without repository changes (console-side setup, operations guides) are indexed in the README documentation table instead.

## [Unreleased]

## [1.13.4] - 2026-07-03

### Security

-   Pin the Minecraft server image by digest — it came from the chart's default (`:latest`) and never appeared in the HelmRelease values

## [1.13.3] - 2026-07-03

### Security

-   Restore digest pins across all app images (Plex, FileBrowser, both playit sidecars, Factorio server) — digests had not survived version bumps

## [1.13.2] - 2026-07-03

> [Factorio Playit Public Access](https://lelopez.io/blog/homelab-v2-11a-factorio-playit-public-access) (amended)

### Fixed

-   Pin playit-agent to 1.0.5 and route its IPC socket to `/tmp` — mirrors the Minecraft fix

## [1.13.1] - 2026-07-03

> [Minecraft Playit Public Access](https://lelopez.io/blog/homelab-v2-12a-minecraft-playit-public-access) (amended)

### Fixed

-   Pin playit-agent to 1.0.5 and route its IPC socket to `/tmp` — `:latest` had pulled an agent that broke the sidecar

## [1.13.0] - 2026-07-03

### Added

-   frpc tunnel client (`frp-tunnel` app): outbound-only reverse tunnel from the cluster to a self-hosted public frps
-   Publish the tunnel URL to plex.tv via `ADVERTISE_IP`, moved from ConfigMap into the sops-encrypted Secret

## [1.12.0] - 2026-04-11

### Added

-   FileBrowser sidecar on the Plex pod for browser-based media uploads

## [1.11.0] - 2026-03-07

> [SecureBoot & Encryption Prep](https://lelopez.io/blog/homelab-v3-13-secureboot-encryption-prep)

### Security

-   UEFI SecureBoot and TPM-sealed LUKS2 disk encryption in cluster config; Talos upgraded to v1.12.4

## [1.10.3] - 2026-03-01

> [Plex Performance](https://lelopez.io/blog/homelab-v3-12a-plex-performance)

### Fixed

-   Preserve client IPs with `externalTrafficPolicy: Local`

## [1.10.2] - 2026-03-01

> [Plex Hardening](https://lelopez.io/blog/homelab-v3-12-plex-hardening)

### Changed

-   Remove old unencrypted PVCs and the migration job

## [1.10.1] - 2026-03-01

### Security

-   Switch Plex to encrypted PVCs with read-only media mount

## [1.10.0] - 2026-03-01

### Security

-   Encrypted PVCs and NetworkPolicy for Plex

## [1.9.2] - 2026-03-01

### Fixed

-   Disable new save generation on Factorio startup

## [1.9.1] - 2026-02-28

### Changed

-   Bump Factorio server version

## [1.9.0] - 2026-02-28

> [Factorio Hardening](https://lelopez.io/blog/homelab-v3-11-factorio-hardening)

### Security

-   Encrypted storage, NetworkPolicy, and pinned chart version for Factorio

## [1.8.0] - 2026-02-28

> [Minecraft Hardening](https://lelopez.io/blog/homelab-v3-10-minecraft-hardening)

### Security

-   Encrypted storage, NetworkPolicy, and pinned chart version for Minecraft

## [1.7.2] - 2026-02-28

> [Longhorn Upgrade](https://lelopez.io/blog/homelab-v3-09a-longhorn-upgrade)

### Changed

-   Upgrade Longhorn to 1.9.2

## [1.7.1] - 2026-02-28

### Changed

-   Upgrade Longhorn to 1.8.1

## [1.7.0] - 2026-02-28

> [Longhorn Hardening](https://lelopez.io/blog/homelab-v3-09-longhorn-hardening)

### Security

-   Volume encryption, NetworkPolicy, and pinned chart version for Longhorn

## [1.6.0] - 2026-02-28

> [MetalLB & Ingress Hardening](https://lelopez.io/blog/homelab-v3-08-metallb-ingress-hardening)

### Security

-   Pinned chart versions and NetworkPolicies for MetalLB and ingress-nginx

## [1.5.0] - 2026-02-28

> [Tailscale Hardening](https://lelopez.io/blog/homelab-v3-07-tailscale-hardening)

### Security

-   Pinned chart version, tag-based ACLs, and NetworkPolicy for Tailscale

## [1.4.0] - 2026-02-28

> [Plex LAN Configuration](https://lelopez.io/blog/homelab-v3-06-plex-lan-configuration)

### Changed

-   Static MetalLB IP and `ADVERTISE_IP` for cross-VLAN direct play

## [1.3.0] - 2026-02-26

> [MetalLB Migration](https://lelopez.io/blog/homelab-v3-05-metallb-migration)

### Changed

-   Migrate MetalLB IP pools to the Lab VLAN

## [1.2.0] - 2026-02-26

> [Tailscale Migration](https://lelopez.io/blog/homelab-v3-04-tailscale-migration)

### Changed

-   Migrate Tailscale subnet routes to the Lab VLAN

## [1.1.0] - 2026-02-22

> [Talos Migration](https://lelopez.io/blog/homelab-v3-03-talos-migration)

### Changed

-   Node IPs, gateway, and etcd subnets moved to the Lab VLAN

## [1.0.1] - 2026-02-17

> [MetalLB Talos L2 Fix](https://lelopez.io/blog/homelab-v2-08a-metallb-talos-l2-fix) (amended)

### Fixed

-   Ignore the node exclusion label for MetalLB L2 announcements

## [1.0.0] - 2026-01-08

Complete documentation of the homelab series.

### Added

-   Full README with architecture diagrams and tutorial index
-   CHANGELOG following Keep a Changelog format
-   MIT License

### Documentation

-   13 articles covering hardware through applications
-   4 architecture diagrams (network, infrastructure, remote access, apps)

## [0.12.3] - 2025-12-30

> [Minecraft Bedrock Support](https://lelopez.io/blog/homelab-v2-12c-minecraft-bedrock-support)

### Added

-   Bedrock port exposure for Geyser cross-platform play

## [0.12.2] - 2025-12-30

> [Minecraft Import Existing World](https://lelopez.io/blog/homelab-v2-12b-minecraft-import-existing-world)

### Added

-   Plugins: BentoBox Skyblock, ViaVersion, DirectionHUD
-   World import and restore workflow

## [0.12.1] - 2025-12-30

> [Minecraft playit.gg Public Access](https://lelopez.io/blog/homelab-v2-12a-minecraft-playit-public-access)

### Added

-   playit.gg sidecar for public access

## [0.12.0] - 2025-12-30

> [Minecraft Paper Server](https://lelopez.io/blog/homelab-v2-12-minecraft-paper-server)

### Added

-   Minecraft server with Paper for performance and plugin support
-   Tailscale LAN access via MetalLB LoadBalancer

## [0.11.3] - 2025-12-29

> [Factorio Security Hardening](https://lelopez.io/blog/homelab-v2-11c-factorio-security-hardening)

### Changed

-   Require Factorio.com authentication for multiplayer

## [0.11.2] - 2025-12-28

> [Factorio Import Existing Save](https://lelopez.io/blog/homelab-v2-11b-factorio-import-existing-save)

### Changed

-   Switch to existing save file

## [0.11.1] - 2025-12-28

> [Factorio playit.gg Public Access](https://lelopez.io/blog/homelab-v2-11a-factorio-playit-public-access)

### Added

-   playit.gg UDP tunnel for public access

## [0.11.0] - 2025-12-28

> [Factorio Kubernetes Server](https://lelopez.io/blog/homelab-v2-11-factorio-kubernetes-server)

### Added

-   Factorio dedicated server with persistent saves
-   Tailscale LAN access via MetalLB LoadBalancer

## [0.10.0] - 2025-12-22

> [Plex Intel GPU Transcoding](https://lelopez.io/blog/homelab-v2-10-plex-intel-gpu-transcoding)

### Added

-   Plex Media Server with Intel Arc GPU transcoding

## [0.9.2] - 2025-12-21

> [Tailscale High Availability](https://lelopez.io/blog/homelab-v2-07b-tailscale-high-availability)

### Added

-   Node 3 to subnet router
-   HA with 3 replicas pinned to control plane nodes

## [0.9.1] - 2025-12-20

> [Talos Cluster Expansion and HA](https://lelopez.io/blog/homelab-v2-05b-talos-cluster-expansion-ha)

### Added

-   Third node to cluster

### Changed

-   All nodes promoted to control plane for etcd HA

## [0.9.0] - 2025-12-18

> [Intel Arc Kubernetes DRA](https://lelopez.io/blog/homelab-v2-09-intel-arc-gpu-kubernetes-dra)

### Added

-   Intel GPU resource driver for DRA
-   DRA feature gates and CDI paths in Talos config

## [0.8.0] - 2025-12-18

> [MetalLB, Longhorn, and Ingress-NGINX](https://lelopez.io/blog/homelab-v2-08-metallb-longhorn-ingress-nginx)

### Added

-   MetalLB load balancer with IP pool .40-.79
-   Ingress-NGINX controller
-   Longhorn distributed storage
-   Core services kustomization

## [0.7.0] - 2025-12-17

> [Tailscale Kubernetes Subnet Router](https://lelopez.io/blog/homelab-v2-07-tailscale-kubernetes-subnet-router)

### Added

-   Tailscale operator with subnet router
-   Encrypted OAuth credentials

## [0.6.0] - 2025-12-17

> [Flux CD Kubernetes GitOps](https://lelopez.io/blog/homelab-v2-06-flux-cd-kubernetes-gitops)

### Added

-   Flux v2.7.5 component manifests
-   Flux sync manifests
-   Initial sync for k8s/core

## [0.5.0] - 2025-12-17

> [Talhelper Cluster Bootstrap](https://lelopez.io/blog/homelab-v2-05-talhelper-cluster-bootstrap)

### Added

-   Encrypted Talos secrets
-   Initial cluster/node configuration
-   iscsi-tools extension for Longhorn storage
-   Generated files to gitignore

## [0.4.0] - 2025-12-17

> [SOPS and Age GitOps Secrets](https://lelopez.io/blog/homelab-v2-04-sops-age-gitops-secrets)

### Added

-   Post-commit hook for auto-push
-   Mac files to gitignore
-   README with project overview
-   SOPS encryption configuration

## [0.3.0] - 2025-12-16

> [Talos Linux USB Installation](https://lelopez.io/blog/homelab-v2-03-talos-linux-usb-install)

Talos Linux installed via USB boot on all three nodes.

## [0.2.0] - 2025-12-16

> [UniFi Flat Network Setup](https://lelopez.io/blog/homelab-v2-02-unifi-flat-network-setup)

Flat network configuration with static DHCP reservations for cluster nodes.

## [0.1.0] - 2025-12-16

> [GEEKOM Mini PC Cluster Hardware](https://lelopez.io/blog/homelab-v2-01-geekom-mini-pc-cluster-hardware)

Hardware selection and procurement for 3-node cluster.

## [0.0.0] - 2025-12-16

> [Talos Kubernetes Homelab Series](https://lelopez.io/blog/homelab-v2-00-talos-kubernetes-homelab-series)

Series introduction and goals.

[unreleased]: https://github.com/lelopez-io/homelab/compare/v1.0.0...HEAD
[1.13.4]: https://github.com/lelopez-io/homelab/compare/v1.13.3...v1.13.4
[1.13.3]: https://github.com/lelopez-io/homelab/compare/v1.13.2...v1.13.3
[1.13.2]: https://github.com/lelopez-io/homelab/compare/v1.13.1...v1.13.2
[1.13.1]: https://github.com/lelopez-io/homelab/compare/v1.13.0...v1.13.1
[1.13.0]: https://github.com/lelopez-io/homelab/compare/v1.12.0...v1.13.0
[1.12.0]: https://github.com/lelopez-io/homelab/compare/v1.11.0...v1.12.0
[1.11.0]: https://github.com/lelopez-io/homelab/compare/v1.10.3...v1.11.0
[1.10.3]: https://github.com/lelopez-io/homelab/compare/v1.10.2...v1.10.3
[1.10.2]: https://github.com/lelopez-io/homelab/compare/v1.10.1...v1.10.2
[1.10.1]: https://github.com/lelopez-io/homelab/compare/v1.10.0...v1.10.1
[1.10.0]: https://github.com/lelopez-io/homelab/compare/v1.9.2...v1.10.0
[1.9.2]: https://github.com/lelopez-io/homelab/compare/v1.9.1...v1.9.2
[1.9.1]: https://github.com/lelopez-io/homelab/compare/v1.9.0...v1.9.1
[1.9.0]: https://github.com/lelopez-io/homelab/compare/v1.8.0...v1.9.0
[1.8.0]: https://github.com/lelopez-io/homelab/compare/v1.7.2...v1.8.0
[1.7.2]: https://github.com/lelopez-io/homelab/compare/v1.7.1...v1.7.2
[1.7.1]: https://github.com/lelopez-io/homelab/compare/v1.7.0...v1.7.1
[1.7.0]: https://github.com/lelopez-io/homelab/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/lelopez-io/homelab/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/lelopez-io/homelab/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/lelopez-io/homelab/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/lelopez-io/homelab/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/lelopez-io/homelab/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/lelopez-io/homelab/compare/v1.0.1...v1.1.0
[1.0.1]: https://github.com/lelopez-io/homelab/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/lelopez-io/homelab/compare/v0.12.3...v1.0.0
[0.12.3]: https://github.com/lelopez-io/homelab/compare/v0.12.2...v0.12.3
[0.12.2]: https://github.com/lelopez-io/homelab/compare/v0.12.1...v0.12.2
[0.12.1]: https://github.com/lelopez-io/homelab/compare/v0.12.0...v0.12.1
[0.12.0]: https://github.com/lelopez-io/homelab/compare/v0.11.3...v0.12.0
[0.11.3]: https://github.com/lelopez-io/homelab/compare/v0.11.2...v0.11.3
[0.11.2]: https://github.com/lelopez-io/homelab/compare/v0.11.1...v0.11.2
[0.11.1]: https://github.com/lelopez-io/homelab/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/lelopez-io/homelab/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/lelopez-io/homelab/compare/v0.9.2...v0.10.0
[0.9.2]: https://github.com/lelopez-io/homelab/compare/v0.9.1...v0.9.2
[0.9.1]: https://github.com/lelopez-io/homelab/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/lelopez-io/homelab/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/lelopez-io/homelab/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/lelopez-io/homelab/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/lelopez-io/homelab/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/lelopez-io/homelab/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/lelopez-io/homelab/releases/tag/v0.4.0

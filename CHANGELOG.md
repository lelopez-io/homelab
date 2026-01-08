# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each release corresponds to an article in the [Home Lab V2 tutorial series](https://lelopez.io/blog/homelab-v2-00-talos-kubernetes-homelab-series).

## [Unreleased]

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

#!/usr/bin/env bash
# Install the gate's tools into $1 at pinned versions. Every download is
# checked against a digest written here, so a swapped release asset fails
# the install instead of running.
set -euo pipefail

dest=${1:?usage: install-tools.sh <dir> [tool...]}
shift
tools=" ${*:-kyverno kubeconform helm kubectl yq gh} "
mkdir -p "$dest"
want() { [[ $tools == *" $1 "* ]]; }

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) plat=linux-amd64 ;;
  Darwin-arm64) plat=darwin-arm64 ;;
  *) echo "no pinned tools for $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

# fetch <url> <sha256> <outfile>
fetch() {
  curl -fsSL --retry 3 -o "$3" "$1"
  [ "$(sha256 "$3")" = "$2" ] || { echo "digest mismatch for $1" >&2; exit 1; }
}

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

KYVERNO=1.19.2 KUBECONFORM=v0.8.0 HELM=v4.0.5 KUBECTL=v1.34.1 YQ=v4.54.1 GH=2.95.0

case $plat in
  linux-amd64)
    sum_kyverno=357eaf1df8bc24ddd5a73979fadeaae6a032e8710f11af264229756e4956bf4a
    sum_kubeconform=9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
    sum_helm=730e4e9fbff94168249ddd0b9b1b8c357b7f64815462dd88c6b39f09bf18b814
    sum_kubectl=7721f265e18709862655affba5343e85e1980639395d5754473dafaadcaa69e3
    sum_yq=8e34fc298390875de416e6a4afcb8cabeceb25d9aa8506c1a2f9353cf702ea5f
    sum_gh=25d1e4729e8808c9ed3d613e96ebd3f3e44446f2d368c89d878a71a36ddb3d8c
    kyverno_asset=linux_x86_64 ;;
  darwin-arm64)
    sum_kyverno=7915c03c613c22363a100adb3da8d06f2e2626d9d21a98b97f921795ffdc536f
    sum_kubeconform=f84f4dfbebf4a6b0b230385fa065a39ea35e02608c2b50d025dcf64775a69d67
    sum_helm=b4d04ccf68004604e13878fce4a893711490914512f8759879f848136a9f5fca
    sum_kubectl=d80e5fa36f2b14005e5bb35d3a72818acb1aea9a081af05340a000e5fbdb2f76
    sum_yq=fee511a181bd8b3e6b7da98842b41973bfac8cd3bcd341ed29a584620b5b2844
    sum_gh=3677f9c27965825f9c7d50395473c134edaea4b484373ef6b25de653570a0489
    kyverno_asset=darwin_arm64 ;;
esac

if want kyverno; then
  fetch "https://github.com/kyverno/kyverno/releases/download/v$KYVERNO/kyverno-cli_v${KYVERNO}_$kyverno_asset.tar.gz" "$sum_kyverno" "$tmp/kyverno.tgz"
  tar -xzf "$tmp/kyverno.tgz" -C "$dest" kyverno
fi

if want kubeconform; then
  fetch "https://github.com/yannh/kubeconform/releases/download/$KUBECONFORM/kubeconform-$plat.tar.gz" "$sum_kubeconform" "$tmp/kubeconform.tgz"
  tar -xzf "$tmp/kubeconform.tgz" -C "$dest" kubeconform
fi

if want helm; then
  fetch "https://get.helm.sh/helm-$HELM-$plat.tar.gz" "$sum_helm" "$tmp/helm.tgz"
  tar -xzf "$tmp/helm.tgz" -C "$tmp" "$plat/helm"
  mv "$tmp/$plat/helm" "$dest/helm"
fi

if want kubectl; then
  fetch "https://dl.k8s.io/release/$KUBECTL/bin/${plat/-//}/kubectl" "$sum_kubectl" "$dest/kubectl"
fi

if want yq; then
  fetch "https://github.com/mikefarah/yq/releases/download/$YQ/yq_${plat/-/_}" "$sum_yq" "$dest/yq"
fi

if want gh; then
  if [ "$plat" = linux-amd64 ]; then
    fetch "https://github.com/cli/cli/releases/download/v$GH/gh_${GH}_linux_amd64.tar.gz" "$sum_gh" "$tmp/gh.tgz"
    tar -xzf "$tmp/gh.tgz" -C "$tmp" --strip-components=2 "gh_${GH}_linux_amd64/bin/gh"
  else
    fetch "https://github.com/cli/cli/releases/download/v$GH/gh_${GH}_macOS_arm64.zip" "$sum_gh" "$tmp/gh.zip"
    unzip -qjo "$tmp/gh.zip" "gh_${GH}_macOS_arm64/bin/gh" -d "$tmp"
  fi
  mv "$tmp/gh" "$dest/gh"
fi
chmod +x "$dest"/*

#!/usr/bin/env bash
# Prints the next release version (vX.Y.Z) for a commit, or nothing when the
# commits since the last release don't call for one. CI's Tag job runs it on
# every green push to main; run it locally to preview what a merge releases.
#
#   scripts/next-version.sh [rev]    # rev defaults to HEAD
#
# The bump is the largest one asked for by any commit since the latest stable
# v* tag reachable from rev, read from Conventional Commits subjects
# (https://www.conventionalcommits.org):
#
#   feat: ...                        minor
#   fix: ... / perf: ...             patch
#   type!: ... or a BREAKING CHANGE: major (minor while the version is 0.x)
#   footer in the body
#   anything else (docs:, ci:,       no release
#   chore:, a non-conventional
#   subject, ...)
#
# A scope is allowed (feat(admin): ...). Merge commits are skipped; their
# branches' commits are read instead. Pre-release tags (v0.2.0-rc.1) are not
# a baseline: the next stable version is computed from the last stable one.
# Moving to 1.0.0 is a deliberate step: push the v1.0.0 tag by hand.
#
# The reasoning goes to stderr, the version alone to stdout.
set -euo pipefail

rev=${1:-HEAD}

last=$(git describe --tags --abbrev=0 \
	--match 'v[0-9]*.[0-9]*.[0-9]*' --exclude '*-*' "$rev" 2>/dev/null || true)
if [[ -n $last ]]; then
	range="$last..$rev"
	base=${last#v}
else
	range=$rev
	base=0.0.0
fi
if ! [[ $base =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
	echo "next-version: latest tag $last is not vX.Y.Z" >&2
	exit 1
fi
major=${BASH_REMATCH[1]} minor=${BASH_REMATCH[2]} patch=${BASH_REMATCH[3]}

# 0 none, 1 patch, 2 minor, 3 major
bump=0
names=(none patch minor major)
conventional='^[a-zA-Z]+(\([^()]+\))?(!)?: '

# One record per commit: hash, subject and body separated by \x1f, records
# terminated by \x1e, so bodies can hold any text.
while IFS=$'\x1f' read -r -d $'\x1e' hash subject body; do
	hash=${hash#$'\n'}
	level=0
	if [[ $subject =~ $conventional ]]; then
		type=${subject%%[(!:]*}
		type=${type,,}
		case $type in
		feat) level=2 ;;
		fix | perf) level=1 ;;
		esac
		if [[ -n ${BASH_REMATCH[2]} ]]; then level=3; fi
	fi
	if grep -qE '^BREAKING[ -]CHANGE: ' <<<"$body"; then level=3; fi
	echo "  ${hash:0:9} ${names[level]}: $subject" >&2
	if ((level > bump)); then bump=$level; fi
done < <(git log --no-merges --format='%H%x1f%s%x1f%b%x1e' "$range")

echo "next-version: ${last:-no release yet}, ${names[bump]} bump from $range" >&2

# Under 1.0.0 the public API isn't stable (semver item 4), so a breaking
# change raises the minor version rather than leaving 0.x.
if ((bump == 3 && major == 0)); then bump=2; fi

case $bump in
0) exit 0 ;;
1) patch=$((patch + 1)) ;;
2) minor=$((minor + 1)) patch=0 ;;
3) major=$((major + 1)) minor=0 patch=0 ;;
esac
echo "v$major.$minor.$patch"

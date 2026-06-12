#!/usr/bin/env python3
"""applicationsets/apps-appset.yaml の二重リスト (git generator exclude / list
generator elements) を apps/ の実状態と突合する CI 向け検証スクリプト。

apps-appset.yaml は同じ「namespace がディレクトリ名と異なる app 集合」を 2 回
別表現で列挙している:
  (1) git generator の `exclude: true` 付き `path: apps/<name>` 群
  (2) list generator の `elements:` 群 (path.basename + namespace)
片方の更新漏れで (a) basename と同名の空 namespace 量産、(b) app 未生成 の
どちらかが静かに起きる。CI 不在なので検知する仕組みが無かった。

本スクリプトは apps/*/kustomization.yaml の top-level `namespace:` を実際に読み、
「namespace != ディレクトリ名」の app 集合 (= 期待値) を算出し、それが
exclude リスト・list elements の双方と完全一致するかを検証する。さらに list
elements の namespace 値が実 kustomization.yaml の namespace と一致するかも見る。
差分があれば内容を stderr に出して exit 1。

PyYAML 等の外部依存を避け、stdlib の正規表現で必要なフィールドのみ抽出する。
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
APPS = REPO / "apps"
APPSET = HERE / "apps-appset.yaml"

# git generator の exclude は apps/_templates も含むが、それは namespace 突合の
# 対象外 (テンプレート置き場) なので期待値・抽出の双方から除く。
IGNORED_DIRS = {"_templates"}


def app_namespace(kustomization: Path):
    """kustomization.yaml の top-level `namespace:` を返す。無ければ None。

    document scalar の top-level キーのみを対象とするため、行頭 (インデント無し)
    の `namespace:` だけを拾う。リソース内 metadata.namespace は対象外。
    """
    for line in kustomization.read_text().splitlines():
        m = re.match(r"^namespace:\s*(\S+)", line)
        if m:
            return m.group(1)
    return None


def expected_overrides():
    """apps/ を走査し namespace != ディレクトリ名の {dir: namespace} を返す。"""
    out = {}
    for d in sorted(APPS.iterdir()):
        if not d.is_dir() or d.name in IGNORED_DIRS:
            continue
        k = d / "kustomization.yaml"
        if not k.exists():
            continue
        ns = app_namespace(k)
        if ns is not None and ns != d.name:
            out[d.name] = ns
    return out


def parse_exclude_dirs(text):
    """git generator の `exclude: true` が付いた apps/<name> 群を返す。

    `- path: apps/<name>` の直後 (次の `- path:` までの間) に `exclude: true`
    があるものを抽出する。
    """
    excluded = set()
    pending = None
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"-\s*path:\s*apps/([^\s/]+)\s*$", line)
        if m:
            pending = m.group(1)
            continue
        if pending is not None and re.match(r"exclude:\s*true\b", line):
            excluded.add(pending)
            pending = None
    excluded -= IGNORED_DIRS
    return excluded


def parse_list_elements(text):
    """list generator の elements を {basename: namespace} で返す。

    各 element は
        - path:
            path: apps/<name>
            basename: <name>
          namespace: <ns>
    の形。list generator セクション (`- list:` 以降) に限定して走査する。
    """
    lines = text.splitlines()
    start = None
    for i, raw in enumerate(lines):
        if re.match(r"\s*-\s*list:\s*$", raw):
            start = i
            break
    if start is None:
        return {}
    elements = {}
    cur_basename = None
    for raw in lines[start + 1:]:
        # template: セクションに入ったら list generator は終わり。
        if re.match(r"\s*template:\s*$", raw):
            break
        bm = re.search(r"\bbasename:\s*(\S+)", raw)
        if bm:
            cur_basename = bm.group(1)
            continue
        nm = re.match(r"\s+namespace:\s*(\S+)", raw)
        if nm and cur_basename is not None:
            elements[cur_basename] = nm.group(1)
            cur_basename = None
    return elements


def main():
    text = APPSET.read_text()
    expected = expected_overrides()          # {dir: ns} from apps/
    exclude = parse_exclude_dirs(text)       # set of dirs
    elements = parse_list_elements(text)     # {basename: ns}

    errors = []

    exp_dirs = set(expected)
    if exclude != exp_dirs:
        only_excl = sorted(exclude - exp_dirs)
        only_exp = sorted(exp_dirs - exclude)
        if only_excl:
            errors.append(
                "git generator exclude に余分な app (apps/ では namespace==dir): "
                + ", ".join(only_excl)
            )
        if only_exp:
            errors.append(
                "namespace != dir なのに git generator exclude に無い app: "
                + ", ".join(only_exp)
            )

    elem_dirs = set(elements)
    if elem_dirs != exp_dirs:
        only_elem = sorted(elem_dirs - exp_dirs)
        only_exp = sorted(exp_dirs - elem_dirs)
        if only_elem:
            errors.append(
                "list generator elements に余分な app: " + ", ".join(only_elem)
            )
        if only_exp:
            errors.append(
                "namespace != dir なのに list generator elements に無い app: "
                + ", ".join(only_exp)
            )

    # list elements の namespace 値が実 kustomization の namespace と一致するか。
    for name, ns in sorted(elements.items()):
        want = expected.get(name)
        if want is not None and ns != want:
            errors.append(
                f"list element {name} の namespace={ns} が "
                f"kustomization.yaml の namespace={want} と不一致"
            )

    if errors:
        sys.stderr.write(
            "apps-appset.yaml の二重リストが apps/ の実状態と一致しません:\n"
        )
        for e in errors:
            sys.stderr.write("  - " + e + "\n")
        sys.exit(1)

    print(
        f"apps-appset.yaml OK: namespace override {len(expected)} 件が "
        "git exclude / list elements の双方と一致しています。"
    )


if __name__ == "__main__":
    main()

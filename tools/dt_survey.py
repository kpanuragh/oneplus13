#!/usr/bin/env python3
"""Turn downstream (Android) DTBs/DTBO overlays into a hardware inventory.

Downstream OnePlus device trees spread one board over a SoC base DTB (in
vendor_boot) and dozens of overlays (in dtbo.img). This tool walks either
kind and prints every enabled device that has a `compatible`, together with
its bus, address, GPIOs and supplies. Phandle references resolve to their
labels (e.g. `<&tlmm 98 0>`), so the output can be mapped onto mainline
nodes by hand.

    python3 tools/dt_survey.py out/dtbo/dtbo.03.dtbo
    python3 tools/dt_survey.py --project 23821 out/dtbo/*.dtbo
    python3 tools/dt_survey.py --markdown out/vendor_boot/dtb.00.dtb > hw.md

`--project` keeps only overlays whose `oplus,project-id` contains that ID.
OnePlus 13 uses 23821, and 23893/23894/23895 for the other regional SKUs.
"""

import argparse
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fdt as fdtlib  # noqa: E402

REF_PROPS_SUFFIX = ("-gpio", "-gpios", "gpios", "gpio", "-supply")
SKIP_COMPAT_PREFIX = ("qcom,msm-cdc-pinctrl",)


class Resolver:
    """Resolve phandle cells inside properties back to labels."""

    def __init__(self, f):
        self.f = f
        self._phandle_nodes = None
        self.by_phandle = {}
        labels = f.labels()
        path_to_label = {v: k for k, v in labels.items()}
        for ph, node in f.phandles().items():
            self.by_phandle[ph] = path_to_label.get(node.path, node.path)
        # overlays: __fixups__ { label = "/fragment@0/__overlay__/x:prop:off", ... }
        self.fixups = {}
        self.fixup_props = set()
        fx = f.root.child("__fixups__")
        if fx:
            for label in fx.props:
                for ent in fx.strlist(label):
                    path, prop, off = ent.rsplit(":", 2)
                    self.fixups[(path, prop, int(off))] = label
                    self.fixup_props.add((path, prop))

    def format_ref_prop(self, node, prop):
        cells = node.cells(prop)
        if cells is None:
            return None
        if self._phandle_nodes is None:
            self._phandle_nodes = self.f.phandles()
        phandle_nodes = self._phandle_nodes
        out = []
        next_ref = 0          # index of the next cell expected to be a phandle
        for i, c in enumerate(cells):
            label = self.fixups.get((node.path, prop, i * 4))
            if label is None and i == next_ref and c in self.by_phandle:
                label = self.by_phandle[c]
                # Skip over the specifier cells of a resolvable provider.
                target = phandle_nodes.get(c)
                n = (target.cells("#gpio-cells") or target.cells("#interrupt-cells")
                     or [0])[0] if target else 0
                next_ref = i + 1 + n
            elif label is not None:
                next_ref = -1  # overlays: rely on __fixups__ only
            out.append("&" + label if label else str(c))
        return "<" + " ".join(out) + ">"

    def fragment_target(self, frag):
        tp = frag.str("target-path")
        if tp:
            return tp
        label = self.fixups.get((frag.path, "target", 0))
        if label:
            return "&" + label
        c = frag.cells("target")
        if c and c[0] in self.by_phandle:
            return "&" + self.by_phandle[c[0]]
        return "?"


def _reg(node):
    c = node.cells("reg")
    if not c:
        return ""
    return "@" + ",".join("0x%x" % x for x in c[:2])


def survey(f, resolver):
    """Yield (container, node) for interesting enabled nodes."""
    roots = []
    if f.is_overlay():
        for frag in f.root.children:
            ov = frag.child("__overlay__")
            if ov is not None:
                roots.append((resolver.fragment_target(frag), ov))
    else:
        roots.append(("/", f.root))
    for target, root in roots:
        for n in root.walk():
            if n is root or not n.props.get("compatible") or not n.enabled():
                continue
            compat = n.strlist("compatible")
            if compat and compat[0].startswith(SKIP_COMPAT_PREFIX):
                continue
            parent = n.parent
            rel = parent.path[len(root.path):].lstrip("/")
            container = target if not rel else (target.rstrip("/") + "/" + rel)
            yield container, n


def main():
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--project", type=int, help="only overlays for this oplus project id")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--grep", help="only devices whose compatible contains this text")
    a = ap.parse_args()

    for path in a.files:
        with open(path, "rb") as fh:
            data = fh.read()
        for off, f in fdtlib.find_fdts(data):
            proj = f.root.cells("oplus,project-id") or []
            if a.project and a.project not in proj:
                # Overlays keep project ids at the root; skip non-matching ones.
                continue
            res = Resolver(f)
            title = "%s (+0x%x) model=%r project-id=%s msm-id=%s board-id=%s" % (
                path, off, f.root.str("model", ""), proj or "-",
                f.root.cells("qcom,msm-id"), f.root.cells("qcom,board-id"))
            rows = []
            for container, n in survey(f, res):
                compat = ", ".join(n.strlist("compatible"))
                if a.grep and a.grep not in compat:
                    continue
                refs = []
                for p in n.props:
                    if (p.endswith(REF_PROPS_SUFFIX) or (n.path, p) in res.fixup_props
                            or p in ("interrupts", "interrupts-extended")) \
                            and not p.startswith("pinctrl-"):
                        v = res.format_ref_prop(n, p)
                        if v:
                            refs.append("%s=%s" % (p, v))
                rows.append((container, n.name, _reg(n), compat, "; ".join(refs)))
            if not rows:
                continue
            if a.markdown:
                print("\n### %s\n" % title)
                print("| parent | node | compatible | refs |")
                print("|---|---|---|---|")
                for c, name, reg, compat, refs in rows:
                    print("| `%s` | `%s` | `%s` | %s |" % (c, name, compat,
                                                          refs.replace("|", "\\|")))
            else:
                print("== " + title)
                for c, name, reg, compat, refs in rows:
                    print("  %-28s %-34s %s" % (c[:28], name[:34], compat))
                    if refs:
                        print("  %-28s   %s" % ("", refs))
    return 0


if __name__ == "__main__":
    sys.exit(main())

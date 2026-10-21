"""Structure of the layer stack: the flat entry list <-> a tree of folders, layers, masks.

`material.essence.layers` is a flat list, stored bottom-to-top, because that is what a
Blender UI list can show. Every entry has a `uid` and a `parent` (the uid of its owner):

    folder   top level, owns layers and masks
    layer    top level or inside a folder, owns masks
    mask     owned by a layer or a folder

The list is the post-order of that tree: everything an entry owns sits directly below it
(lower index), so with the list drawn reversed an owner is followed by its masks and, for a
folder, its layers. Inside an owner, layers/masks are ordered bottom-to-top as well, and a
folder's masks sit above its layers (so they are listed first under the folder).

All edits go through `Tree`: change the tree, then `apply()` to reorder the flat list.
"""

import uuid


def new_uid():
    return uuid.uuid4().hex[:8]


def ensure_uids(layers):
    """Give entries made before uids existed one. Returns True if anything changed."""
    changed = False
    for e in layers:
        if not e.uid:
            e.uid = new_uid()
            changed = True
    return changed


class Tree:
    def __init__(self, layers):
        self.layers = layers
        self.kind = {e.uid: e.kind for e in layers}
        self.parent = {}
        self.roots = []
        self.kids = {}     # folder uid -> [layer uid], bottom-to-top
        self.masks = {}    # owner uid -> [mask uid], bottom-to-top
        self.orphans = []  # entries whose owner is missing; kept so nothing is lost
        for e in layers:
            p = e.parent if e.parent in self.kind else ""
            if e.kind == 'MASK':
                if p:
                    self.masks.setdefault(p, []).append(e.uid)
                else:
                    self.orphans.append(e.uid)
                    continue
            elif p and self.kind[p] == 'FOLDER' and e.kind == 'LAYER':
                self.kids.setdefault(p, []).append(e.uid)
            else:
                p = ""
                self.roots.append(e.uid)
            self.parent[e.uid] = p

    # -- queries ---------------------------------------------------------------------

    def owner_of(self, uid):
        """The layer/folder a mask belongs to, else the entry itself."""
        return self.parent[uid] if self.kind[uid] == 'MASK' else uid

    def siblings(self, uid):
        p = self.parent[uid]
        if self.kind[uid] == 'MASK':
            return self.masks[p]
        return self.roots if not p else self.kids[p]

    # -- edits -----------------------------------------------------------------------

    def insert_after(self, uid, anchor):
        """Move `uid` to sit just above `anchor` among their siblings."""
        sib = self.siblings(uid)
        sib.remove(uid)
        sib.insert(self.siblings(anchor).index(anchor) + 1, uid)

    def move(self, uid, up):
        """One step up or down. Layers cross folder borders (up into a folder joins it as
        its bottom child, down into one as its top child, past the end leaves it);
        folders and masks only reorder among their siblings. False if it cannot move."""
        sib = self.siblings(uid)
        i = sib.index(uid)
        j = i + (1 if up else -1)
        if self.kind[uid] == 'LAYER':
            par = self.parent[uid]
            if 0 <= j < len(sib):
                nb = sib[j]
                if not par and self.kind[nb] == 'FOLDER':
                    sib.pop(i)
                    kids = self.kids.setdefault(nb, [])
                    kids.insert(0 if up else len(kids), uid)
                    self.parent[uid] = nb
                    return True
            elif par:
                sib.pop(i)
                self.roots.insert(self.roots.index(par) + (1 if up else 0), uid)
                self.parent[uid] = ""
                return True
        if not 0 <= j < len(sib):
            return False
        sib[i], sib[j] = sib[j], sib[i]
        return True

    # -- result ----------------------------------------------------------------------

    def order(self):
        out = list(self.orphans)

        def emit(u):
            for c in self.kids.get(u, ()):
                emit(c)
            out.extend(self.masks.get(u, ()))
            out.append(u)

        for r in self.roots:
            emit(r)
        return out

    def apply(self):
        """Reorder the flat list and write the parents back."""
        cur = [e.uid for e in self.layers]
        for t, u in enumerate(self.order()):
            p = cur.index(u)
            if p != t:
                self.layers.move(p, t)
                cur.insert(t, cur.pop(p))
        for e in self.layers:
            if e.uid in self.parent and e.parent != self.parent[e.uid]:
                e.parent = self.parent[e.uid]

    def index(self, uid):
        for i, e in enumerate(self.layers):
            if e.uid == uid:
                return i
        return -1


def mask_entries(layers, owner_uid):
    """The masks of an owner, bottom-to-top."""
    return [e for e in layers if e.kind == 'MASK' and e.parent == owner_uid]


def remove_entries(layers, uid):
    """Remove an entry and what it owns. A folder's layers are kept and moved to the top
    level. Returns the removed entries' (node_name, mask_node_name) pairs so the caller can
    delete their nodes."""
    target = next(e for e in layers if e.uid == uid)
    doomed = {uid}
    if target.kind != 'MASK':
        doomed.update(e.uid for e in layers if e.kind == 'MASK' and e.parent == uid)
    if target.kind == 'FOLDER':
        for e in layers:
            if e.kind == 'LAYER' and e.parent == uid:
                e.parent = ""
    nodes = []
    for i in range(len(layers) - 1, -1, -1):
        if layers[i].uid in doomed:
            nodes.append((layers[i].node_name, layers[i].mask_node_name))
            layers.remove(i)
    return nodes

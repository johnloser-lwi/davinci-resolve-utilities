# Connect Selected Nodes
#
# Connects the selected Fusion nodes without dragging noodles.
#
#   2 nodes  : first-clicked feeds the last-clicked (the active/yellow node).
#              Fusion does not expose click order via GetToolList, so the
#              direction comes from comp.ActiveTool, which tracks the last
#              node you clicked. If that is unavailable the script falls back
#              to flow position (left feeds right).
#   3+ nodes : click order is unavailable for more than one node, so they are
#              sorted left-to-right in the flow and chained A -> B -> C.
#
# Destination input priority:
#   1. EffectMask, when the source is a mask node — matches dragging a mask on
#   2. the first unconnected main input
#   3. EffectMask, when there is no free main input. This covers generators
#      such as Background, Text+ and Fast Noise, which have no image input at
#      all, and nodes whose image inputs are already full.
#   4. the primary input, replacing what is already there (always reported)
#
# Everything runs inside one undo step, so Ctrl+Z reverts the whole run.

MAX_MAIN_INPUTS = 8

resolve = bmd.scriptapp("Resolve")
fusion = resolve.Fusion()


def tool_name(tool):
    try:
        return tool.Name
    except Exception:
        return "?"


def reg_id(tool):
    try:
        return tool.GetAttrs()["TOOLS_RegID"] or ""
    except Exception:
        return ""


def looks_like_mask(tool):
    """Polygon / Rectangle / BSpline / Bitmap masks all end in 'Mask'."""
    return reg_id(tool).endswith("Mask")


def flow_x(flow, tool):
    """X position in the node graph; falls back to 0 when unavailable."""
    if flow:
        try:
            pos = flow.GetPosTable(tool)
            if pos:
                # Comes back as a 1-indexed table -> {1.0: x, 2.0: y}
                return float(list(pos.values())[0])
        except Exception:
            pass
        try:
            pos = flow.GetPos(tool)
            if isinstance(pos, (list, tuple)) and pos:
                return float(pos[0])
            if pos is not None:
                return float(pos)
        except Exception:
            pass
    return 0.0


def input_id(inp):
    try:
        return inp.GetAttrs()["INPS_ID"] or ""
    except Exception:
        return ""


def is_free(inp):
    try:
        return inp.GetConnectedOutput() is None
    except Exception:
        return False


def find_effect_mask(tool):
    try:
        inputs = tool.GetInputList() or {}
    except Exception:
        return None
    for inp in inputs.values():
        if input_id(inp) == "EffectMask" and is_free(inp):
            return inp
    return None


def pick_input(source, dest):
    """Choose the destination input. Returns (input, label, replaced)."""
    # Masks prefer the EffectMask leg, the same as dragging one on
    if looks_like_mask(source):
        mask_input = find_effect_mask(dest)
        if mask_input:
            return mask_input, "EffectMask", False

    first_main = None
    for i in range(1, MAX_MAIN_INPUTS + 1):
        try:
            inp = dest.FindMainInput(i)
        except Exception:
            inp = None
        if not inp:
            break
        if first_main is None:
            first_main = inp
        if is_free(inp):
            return inp, input_id(inp) or f"Input {i}", False

    # No free image input. Generators like Background and Text+ have no main
    # input whatsoever, and a fully wired node can still have a free mask, so
    # try EffectMask before resorting to anything destructive.
    mask_input = find_effect_mask(dest)
    if mask_input:
        return mask_input, "EffectMask", False

    # Everything is occupied — reuse the primary input and flag it loudly
    if first_main is not None:
        return first_main, input_id(first_main) or "Input 1", True
    return None, "", False


def connect(source, dest):
    """Wire source's output into a suitable input on dest."""
    inp, label, replaced = pick_input(source, dest)
    if inp is None:
        return False, f"{tool_name(dest)} has no connectable input."

    try:
        out = source.FindMainOutput(1)
    except Exception:
        out = None
    if not out:
        return False, f"{tool_name(source)} has no output to connect."

    try:
        inp.ConnectTo(out)
    except Exception as e:
        return False, f"{tool_name(source)} -> {tool_name(dest)}: {e}"

    note = "  (replaced existing connection)" if replaced else ""
    return True, f"{tool_name(dest)}.{label}  <-  {tool_name(source)}{note}"


comp = fusion.GetCurrentComp()

if not comp:
    print("No Fusion comp is open. Open a clip on the Fusion page and try again.")
else:
    try:
        selected = list((comp.GetToolList(True) or {}).values())
    except Exception as e:
        selected = []
        print(f"Could not read the selection: {e}")

    if len(selected) < 2:
        print(f"Select at least two nodes to connect (found {len(selected)}).")
    else:
        try:
            flow = comp.CurrentFrame.FlowView
        except Exception:
            flow = None

        pairs = []
        if len(selected) == 2:
            # GetToolList order is meaningless, so ask which node is active —
            # that is the one clicked last.
            active = None
            try:
                active = comp.ActiveTool
            except Exception:
                active = None

            active_name = tool_name(active) if active else None
            names = [tool_name(t) for t in selected]

            if active_name in names:
                dest = selected[names.index(active_name)]
                source = selected[1 - names.index(active_name)]
                how = "click order (active node receives)"
            else:
                ordered = sorted(selected, key=lambda t: flow_x(flow, t))
                source, dest = ordered[0], ordered[1]
                how = "flow position (no active node found)"
            pairs.append((source, dest))
        else:
            # Click order is unavailable beyond the active node — chain by position
            ordered = sorted(selected, key=lambda t: flow_x(flow, t))
            pairs = list(zip(ordered, ordered[1:]))
            how = "flow position, left to right"

        print(f"Connecting {len(selected)} node(s) using {how}...")

        try:
            comp.Lock()
        except Exception:
            pass
        try:
            comp.StartUndo("Connect Selected Nodes")
        except Exception:
            pass

        made, failures = 0, []
        for source, dest in pairs:
            ok, message = connect(source, dest)
            print(("  " if ok else "  FAILED: ") + message)
            if ok:
                made += 1
            else:
                failures.append(message)

        try:
            comp.EndUndo(True)
        except Exception:
            pass
        try:
            comp.Unlock()
        except Exception:
            pass

        print(f"\nDone. {made} of {len(pairs)} connection(s) made.")
        if failures:
            print(f"{len(failures)} failed — see above.")

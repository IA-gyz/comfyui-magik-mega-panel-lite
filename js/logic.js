function remainingPrompts(next, total, pending = 0) {
    if (!Number.isFinite(next) || !Number.isFinite(total) || total < 1) return 0;
    return Math.max(0, total - Math.max(0, Math.trunc(next)) + 1 - pending);
}

const PROMPTER_TYPES = new Set([
    "MagikPrompterA2S", "MagikMetaPrompter", "MagikAnimaXMetaPrompter",
    "MagikAnimaMetaPrompter", "MagikNLMetaPrompterJSON", "MagikNLMetaPrompter",
]);
function isNLMetaPrompter(node) {
    return String(node?.type || "").replace(/[^a-z0-9]/gi, "").toLowerCase()
        .startsWith("magiknlmetaprompter");
}
function isSupportedPrompter(node) {
    return isNLMetaPrompter(node) || PROMPTER_TYPES.has(node?.type);
}
function prompterSetting(node, name) {
    // Current NL nodes expose virtual controls through their settings store.
    // Read it first: its values can precede the serialized widget by a microtask.
    const proxy = node?._magikSettings?.proxies?.get?.(name);
    if (proxy) return proxy.value;
    const normalized = value => String(value).replace(/[^a-z0-9]/gi, "").toLowerCase();
    const native = node?.widgets?.find(w => normalized(w.name) === normalized(name));
    if (native) return native.value;
    const settings = node?.widgets?.find(w => w.name === "settings");
    try {
        const values = typeof settings?.value === "string" ? JSON.parse(settings.value) : settings?.value;
        if (values && typeof values === "object" && !Array.isArray(values)) {
            const key = Object.keys(values).find(k => normalized(k) === normalized(name));
            if (key != null) return values[key];
        }
    } catch (_) {}
    return undefined;
}
function prompterTotal(node) {
    const facial = isNLMetaPrompter(node) ? prompterSetting(node, "Facial") : undefined;
    const value = Number(facial ?? prompterSetting(node, "max_index"));
    return Number.isFinite(value) && value > 0 ? Math.trunc(value) : 0;
}
function resolvePrompter(graph, counter, manualId) {
    const candidates = (graph?._nodes || []).filter(isSupportedPrompter);
    if (manualId != null && manualId !== "") {
        const node = candidates.find(n => String(n.id) === String(manualId));
        return { node: node || null, reason: node ? "manual" : "missing", candidates };
    }
    // Prefer NL variants anywhere in the workflow. Connections disambiguate
    // multiple candidates, but are not required for a single present variant.
    const nlCandidates = candidates.filter(isNLMetaPrompter);
    const automaticCandidates = nlCandidates.length ? nlCandidates : candidates;
    const eligible = new Set(automaticCandidates.map(n => String(n.id)));
    // Breadth-first search chooses the nearest downstream eligible prompter.
    // Equal-distance candidates are ambiguous; never choose by graph order.
    const seen = new Set([String(counter.id)]);
    let layer = [counter];
    while (layer.length) {
        const next = [];
        for (const node of layer) for (const output of node.outputs || []) {
            for (const linkId of output.links || []) {
                const link = graph.links instanceof Map ? graph.links.get(linkId) : graph.links?.[linkId];
                const target = graph.getNodeById(link?.target_id);
                if (!target || seen.has(String(target.id))) continue;
                seen.add(String(target.id));
                next.push(target);
            }
        }
        const found = next.filter(n => eligible.has(String(n.id)));
        if (found.length === 1) return { node: found[0], reason: "connected", candidates };
        if (found.length > 1) return { node: null, reason: "ambiguous", candidates };
        layer = next;
    }
    if (nlCandidates.length === 1) return { node: nlCandidates[0], reason: "present", candidates };
    if (nlCandidates.length > 1) return { node: null, reason: "ambiguous", candidates };
    return { node: null, reason: candidates.length ? "disconnected" : "none", candidates };
}


const PHASE_THRESHOLDS = [
    "Posing", "Teasing", "Groping", "Group_Groping", "Cunni",
    "BJ", "Action", "Anaction", "Group_BJ", "Group",
];
function phaseStarts(node) {
    let previous = 0;
    return PHASE_THRESHOLDS.map(name => {
        const raw = prompterSetting(node, name);
        if (raw == null || raw === "" || !Number.isFinite(Number(raw))) return null;
        const threshold = Math.max(Math.trunc(Number(raw)), previous + 1);
        previous = threshold;
        const start = threshold + 1;
        return Number.isSafeInteger(start) && start <= 999999 ? start : null;
    });
}
export { remainingPrompts, resolvePrompter, prompterTotal, phaseStarts };


def normalized(values):
    return {str(value).strip().casefold() for value in values if str(value).strip()}


def search(repository, query):
    results = []
    for record in repository.all(query.source):
        memory = record["memory"]
        if memory["incident_id"] == query.exclude_incident_id:
            continue
        # Explicit type and service are filters: never confuse causes just because tags overlap.
        if query.incident_type and memory["incident_type"].casefold() != query.incident_type.casefold():
            continue
        if query.service and memory["service"].casefold() != query.service.casefold():
            continue
        features = (record["context"].get("anomaly") or {}).get("features", {})
        tags_overlap = normalized(query.tags) & normalized(memory["tags"])
        features_overlap = normalized(query.features) & normalized(features)
        reasons = []
        score = 0.0
        for criterion, weight in (("incident_type", 4), ("service", 3)):
            if getattr(query, criterion):
                score += weight
                reasons.append(f"same {criterion}")
        for label, overlap, requested, available in (
            ("tags", tags_overlap, query.tags, memory["tags"]),
            ("features", features_overlap, query.features, features),
        ):
            if overlap:
                score += len(overlap) / len(normalized(requested) | normalized(available))
                reasons.append(f"overlapping {label}: {', '.join(sorted(overlap))}")
        if score:
            results.append({"record": record, "score": round(score, 4), "reasons": reasons})
    results.sort(key=lambda r: (-r["score"], r["record"]["memory"]["incident_id"]))
    return {"status": "ok", "source": query.source, "results": results[:query.limit], "total_matches": len(results)}

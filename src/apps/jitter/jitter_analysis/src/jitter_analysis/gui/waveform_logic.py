from __future__ import annotations


def has_waveform_data(waveform_records, waveform_index) -> bool:
    return any(waveform_records.get(pv_id) for pv_id in waveform_records) or any(
        waveform_index.get(pv_id) for pv_id in waveform_index
    )


def waveform_ids_in_current_run(details: dict[str, object], waveform_records, waveform_index, selected_waveform_ids) -> list[str]:
    explicit_ids = [
        str(item).strip()
        for item in details.get("waveform_object_ids", [])
        if str(item).strip()
    ]
    if explicit_ids:
        return explicit_ids
    inferred_ids = list(dict.fromkeys(list(waveform_records) + list(waveform_index)))
    if inferred_ids:
        return inferred_ids
    return list(selected_waveform_ids)


def waveform_record_counts(waveform_ids, waveform_records, waveform_index) -> dict[str, int]:
    counts: dict[str, int] = {}
    for pv_id in waveform_ids:
        if pv_id in waveform_records:
            counts[pv_id] = len(waveform_records.get(pv_id, []))
        else:
            counts[pv_id] = len(waveform_index.get(pv_id, []))
    return counts


def waveform_max_length_hint(waveform_records, waveform_index, analysis_result: dict[str, object] | None = None) -> int:
    max_length = 0
    for records in waveform_records.values():
        for record in records:
            max_length = max(max_length, len(record.values))
    for entries in waveform_index.values():
        for entry in entries:
            max_length = max(max_length, int(entry.length))
    if analysis_result:
        max_length = max(max_length, int(analysis_result.get("max_waveform_length", 0)))
    return max_length


def waveform_counts_signature(waveform_ids, waveform_records, waveform_index) -> list[tuple[str, int, int]]:
    counts_signature = []
    for pv_id in waveform_ids:
        if pv_id in waveform_records and waveform_records.get(pv_id):
            tail_record = waveform_records[pv_id][-1]
            counts_signature.append(
                (
                    pv_id,
                    len(waveform_records[pv_id]),
                    int(tail_record.batch_index) if tail_record.batch_index is not None else -1,
                )
            )
        else:
            entries = waveform_index.get(pv_id, [])
            tail_entry = entries[-1] if entries else None
            counts_signature.append(
                (
                    pv_id,
                    len(entries),
                    int(tail_entry.batch_index) if tail_entry is not None and tail_entry.batch_index is not None else -1,
                )
            )
    return counts_signature


def group_waveform_index_entries(entries) -> dict[str, list[object]]:
    grouped: dict[str, list[object]] = {}
    for entry in entries:
        grouped.setdefault(entry.pv_id, []).append(entry)
    return grouped

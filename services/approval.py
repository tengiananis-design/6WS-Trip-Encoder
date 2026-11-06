"""Shared server-side approval checks for analyzed batches."""


def approval_issues(result: dict) -> list[str]:
    issues = []
    if result.get("overall_review_required") is not False:
        issues.append("Batch-level review is still required.")
    for key, label in (
        ("unresolved_items", "unresolved item(s)"),
        ("unidentified_documents", "unidentified document(s)"),
        ("extraction_errors", "PDF extraction error(s)"),
        ("identification_errors", "document identification error(s)"),
    ):
        values = result.get(key) or []
        if values:
            issues.append(f"{len(values)} {label} remain.")

    groups = result.get("driver_groups") or []
    for group in groups:
        driver = (group.get("driver") or {}).get("name") or "Unknown driver"
        if group.get("overall_review_required", True):
            issues.append(f"{driver}: driver package still requires review.")
        if group.get("unresolved_items"):
            issues.append(f"{driver}: unresolved item(s) remain.")
        for collection in ("trips", "toll_reimbursements", "parking_reimbursements"):
            for record in group.get(collection, []):
                if record.get("review_flags") or record.get("confidence") == "low":
                    issues.append(f"{driver}: records still contain review flags.")
                    break
    return issues


def ensure_approvable(result: dict) -> None:
    issues = approval_issues(result)
    if issues:
        raise ValueError("Resolve review items before approval: " + " ".join(issues))

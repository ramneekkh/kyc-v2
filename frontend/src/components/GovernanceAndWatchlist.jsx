import React, { useState, useEffect } from 'react';
import {
    ShieldAlert,
    CheckCircle,
    AlertTriangle,
    UserCheck,
    Lock,
    Unlock,
    RefreshCw,
    FileCheck,
    History,
    User,
    Users,
    AlertOctagon,
    ChevronDown,
    ChevronUp,
} from 'lucide-react';

const DISPOSITION_OPTIONS = [
    { value: 'TRUE_MATCH_ESCALATE', label: 'True Match — Material Risk (Escalate)' },
    { value: 'FALSE_POSITIVE_DISMISS', label: 'False Positive — Homonym / Not Subject (Dismiss)' },
    { value: 'MITIGATED_ACCEPTABLE', label: 'True Match — Historical / Mitigated Risk (Acceptable with Controls)' },
    { value: 'PENDING_DOCUMENTATION', label: 'Pending Verification — Awaiting Client ID / SOW Documentation' },
];

const DECISION_OPTIONS = [
    { value: 'ONBOARD_STANDARD_CDD', label: 'Proceed with Standard CDD' },
    { value: 'PROCEED_WITH_EDD_CONTROLS', label: 'Proceed with Enhanced Due Diligence (EDD) & Monitoring' },
    { value: 'ESCALATE_TO_MLRO', label: 'Escalate to MLRO / Sanctions Compliance (Hold Onboarding)' },
    { value: 'EXIT_REJECT_RELATIONSHIP', label: 'Reject Onboarding / Exit Relationship (Consider STR Filing)' },
];

const RISK_OPTIONS = ['Low', 'Medium', 'High', 'Critical'];

const strengthBadge = (strength) => {
    switch (strength) {
        case 'CONFIRMED':
            return 'bg-red-900/50 text-red-200 border-red-600';
        case 'STRONG':
            return 'bg-orange-900/50 text-orange-200 border-orange-600';
        default:
            return 'bg-amber-900/40 text-amber-200 border-amber-700';
    }
};

const verdictBadge = (verdict) => {
    switch (verdict) {
        case 'TRUE_MATCH_ESCALATE':
            return 'bg-red-950/60 text-red-300 border-red-700';
        case 'FALSE_POSITIVE_DISMISS':
            return 'bg-emerald-950/60 text-emerald-300 border-emerald-700';
        case 'MITIGATED_ACCEPTABLE':
            return 'bg-blue-950/60 text-blue-300 border-blue-700';
        case 'PENDING_DOCUMENTATION':
            return 'bg-amber-950/60 text-amber-300 border-amber-700';
        default:
            return 'bg-slate-800 text-slate-400 border-slate-700';
    }
};

const stateBadge = (state) => {
    switch (state) {
        case 'APPROVED':
            return 'bg-emerald-900/40 text-emerald-300 border-emerald-700';
        case 'PENDING_CHECKER_APPROVAL':
            return 'bg-amber-900/40 text-amber-300 border-amber-700';
        case 'RETURNED_TO_MAKER':
            return 'bg-red-900/40 text-red-300 border-red-700';
        case 'ESCALATED_MLRO':
            return 'bg-purple-900/40 text-purple-300 border-purple-700';
        case 'IN_MAKER_REVIEW':
            return 'bg-blue-900/40 text-blue-300 border-blue-700';
        default:
            return 'bg-slate-800 text-slate-300 border-slate-700';
    }
};

export const WatchlistScreeningPanel = ({
    watchlistData,
    watchlistStatus,
    onRefreshLists,
    refreshingLists,
    dispositions = {},
    onSaveDisposition,
    savingItemId,
    isCaseLocked,
}) => {
    const [draftVerdicts, setDraftVerdicts] = useState({});
    const [draftRationales, setDraftRationales] = useState({});

    const coverage = watchlistData?.coverage || watchlistStatus;
    const hits = watchlistData?.hits || [];
    const lists = coverage?.lists || [];
    const status = watchlistData?.status || (coverage?.is_complete ? 'READY' : 'LOADING');

    const handleSave = (hit) => {
        const itemId = hit.hit_id;
        const verdict = draftVerdicts[itemId] || dispositions[itemId]?.verdict || 'FALSE_POSITIVE_DISMISS';
        const rationale = draftRationales[itemId] ?? dispositions[itemId]?.rationale ?? '';
        onSaveDisposition({
            item_id: itemId,
            item_type: 'WATCHLIST_HIT',
            item_title: `[${hit.list_id}] ${hit.primary_name}`,
            verdict,
            rationale,
        });
    };

    return (
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-sm space-y-5">
            <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 border-b border-slate-800 pb-4">
                <div className="flex items-start gap-3">
                    <div className={`p-2 rounded-lg border ${
                        status === 'SANCTIONS_HIT_DETECTED'
                            ? 'bg-red-950/60 border-red-700 text-red-400'
                            : status === 'POTENTIAL_MATCH_REVIEW_REQUIRED'
                                ? 'bg-amber-950/60 border-amber-700 text-amber-400'
                                : 'bg-emerald-950/50 border-emerald-800 text-emerald-400'
                    }`}>
                        <ShieldAlert size={20} />
                    </div>
                    <div>
                        <div className="flex items-center gap-2 flex-wrap">
                            <h2 className="text-base font-bold text-slate-100">
                                Structured Sanctions & Watchlist Screening
                            </h2>
                            <span className={`px-2.5 py-0.5 text-[11px] font-bold rounded-full border ${
                                status === 'SANCTIONS_HIT_DETECTED'
                                    ? 'bg-red-900/50 text-red-200 border-red-600'
                                    : status === 'POTENTIAL_MATCH_REVIEW_REQUIRED'
                                        ? 'bg-amber-900/40 text-amber-200 border-amber-700'
                                        : status === 'CLEAR'
                                            ? 'bg-emerald-900/40 text-emerald-300 border-emerald-700'
                                            : 'bg-slate-800 text-slate-300 border-slate-700'
                            }`}>
                                {status === 'SANCTIONS_HIT_DETECTED'
                                    ? `SANCTIONS HIT (${watchlistData?.confirmed_hits + watchlistData?.strong_hits} Confirmed/Strong)`
                                    : status === 'POTENTIAL_MATCH_REVIEW_REQUIRED'
                                        ? `POTENTIAL MATCH (${watchlistData?.potential_hits} Review Required)`
                                        : status === 'CLEAR'
                                            ? '0 SANCTIONS HITS (CLEAR)'
                                            : 'LISTS ACTIVE'}
                            </span>
                        </div>
                        <p className="text-xs text-slate-400 mt-1">
                            {coverage?.description || 'Screens against MAS (Singapore), US Treasury OFAC SDN, UN Security Council, and EU Consolidated Financial Sanctions.'}
                        </p>
                    </div>
                </div>

                {onRefreshLists && (
                    <button
                        onClick={onRefreshLists}
                        disabled={refreshingLists}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-colors disabled:opacity-50"
                    >
                        <RefreshCw size={13} className={refreshingLists ? 'animate-spin text-blue-400' : ''} />
                        {refreshingLists ? 'Syncing Official Lists...' : 'Sync Official Feeds'}
                    </button>
                )}
            </div>

            {/* 4 Authority List Cards */}
            {lists.length > 0 && (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                    {lists.map((lst) => {
                        const listHits = hits.filter(h => h.list_id === lst.list_id);
                        return (
                            <div
                                key={lst.list_id}
                                className={`p-3 rounded-lg border ${
                                    listHits.length > 0
                                        ? 'bg-red-950/30 border-red-800/80'
                                        : lst.loaded
                                            ? 'bg-slate-950/60 border-slate-800'
                                            : 'bg-amber-950/30 border-amber-800'
                                }`}
                            >
                                <div className="flex items-center justify-between gap-1">
                                    <span className="text-xs font-bold text-slate-200">{lst.list_id}</span>
                                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-semibold ${
                                        listHits.length > 0
                                            ? 'bg-red-900/60 text-red-200'
                                            : lst.loaded
                                                ? 'bg-emerald-950/80 text-emerald-300 border border-emerald-800/60'
                                                : 'bg-red-950 text-red-300'
                                    }`}>
                                        {listHits.length > 0 ? `${listHits.length} Hit(s)` : lst.loaded ? 'Active' : 'Failed'}
                                    </span>
                                </div>
                                <p className="text-[11px] text-slate-400 mt-1 truncate" title={lst.short_name}>
                                    {lst.short_name}
                                </p>
                                <div className="flex items-center justify-between mt-2 text-[10px] text-slate-500 font-mono">
                                    <span>{(lst.entity_count || 0).toLocaleString()} entities</span>
                                    {lst.source_sha256 && <span title={`Feed SHA-256: ${lst.source_sha256}`}>#{lst.source_sha256.slice(0, 8)}</span>}
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}

            {/* Watchlist Hits List */}
            {hits.length > 0 && (
                <div className="space-y-3">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-red-300 flex items-center gap-1.5">
                        <AlertOctagon size={14} className="text-red-400" />
                        Watchlist Designation Matches ({hits.length}) — Mandatory Maker Disposition Required
                    </h3>
                    {hits.map((hit) => {
                        const existingDisp = dispositions[hit.hit_id];
                        const currentVerdict = draftVerdicts[hit.hit_id] || existingDisp?.verdict || 'TRUE_MATCH_ESCALATE';
                        const currentRationale = draftRationales[hit.hit_id] ?? existingDisp?.rationale ?? '';

                        return (
                            <div
                                key={hit.hit_id}
                                className="p-4 rounded-xl bg-slate-950/80 border border-red-900/60 space-y-3"
                            >
                                <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-2">
                                    <div>
                                        <div className="flex items-center gap-2 flex-wrap">
                                            <span className={`px-2 py-0.5 text-[10px] font-bold rounded border ${strengthBadge(hit.match_strength)}`}>
                                                {hit.match_strength} ({Math.round((hit.match_score || 0) * 100)}%)
                                            </span>
                                            <span className="text-xs font-bold text-slate-100">
                                                {hit.primary_name}
                                            </span>
                                            <span className="text-[11px] font-mono text-slate-400 bg-slate-900 px-2 py-0.5 rounded border border-slate-800">
                                                {hit.list_id} · {hit.entity_id}
                                            </span>
                                            {existingDisp && (
                                                <span className={`text-[10px] px-2 py-0.5 rounded border font-semibold ${verdictBadge(existingDisp.verdict)}`}>
                                                    Maker: {existingDisp.verdict}
                                                </span>
                                            )}
                                        </div>
                                        <p className="text-xs text-slate-300 mt-1.5 leading-relaxed">
                                            {hit.explanation}
                                        </p>
                                    </div>

                                    <div className="flex flex-wrap gap-1.5 shrink-0">
                                        {hit.dob_corroboration && hit.dob_corroboration !== 'UNAVAILABLE' && (
                                            <span className={`text-[10px] px-2 py-0.5 rounded border font-semibold ${
                                                hit.dob_corroboration === 'MISMATCH'
                                                    ? 'bg-amber-950/60 text-amber-300 border-amber-700'
                                                    : 'bg-red-950/60 text-red-300 border-red-700'
                                            }`}>
                                                DOB: {hit.dob_corroboration} ({hit.birth_date || 'N/A'})
                                            </span>
                                        )}
                                        {hit.programs && (
                                            <span className="text-[10px] px-2 py-0.5 rounded bg-slate-900 text-slate-300 border border-slate-700">
                                                Program: {hit.programs}
                                            </span>
                                        )}
                                    </div>
                                </div>

                                {/* Inline Maker Disposition Control */}
                                {onSaveDisposition && (
                                    <div className="pt-3 border-t border-slate-800/80 grid grid-cols-1 md:grid-cols-12 gap-2 items-end">
                                        <div className="md:col-span-4">
                                            <label className="block text-[10px] font-semibold text-slate-400 uppercase mb-1">
                                                Maker Disposition
                                            </label>
                                            <select
                                                disabled={isCaseLocked}
                                                value={currentVerdict}
                                                onChange={(e) => setDraftVerdicts(prev => ({ ...prev, [hit.hit_id]: e.target.value }))}
                                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-blue-500"
                                            >
                                                {DISPOSITION_OPTIONS.map(opt => (
                                                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                                                ))}
                                            </select>
                                        </div>
                                        <div className="md:col-span-6">
                                            <label className="block text-[10px] font-semibold text-slate-400 uppercase mb-1">
                                                Mandatory Audit Rationale (min 10 chars)
                                            </label>
                                            <input
                                                type="text"
                                                disabled={isCaseLocked}
                                                placeholder="Document DOB/passport comparison or sanctions escalation basis..."
                                                value={currentRationale}
                                                onChange={(e) => setDraftRationales(prev => ({ ...prev, [hit.hit_id]: e.target.value }))}
                                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-blue-500"
                                            />
                                        </div>
                                        <div className="md:col-span-2">
                                            <button
                                                type="button"
                                                disabled={isCaseLocked || savingItemId === hit.hit_id}
                                                onClick={() => handleSave(hit)}
                                                className="w-full px-3 py-1.5 rounded-lg text-xs font-semibold bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50"
                                            >
                                                {savingItemId === hit.hit_id ? 'Saving...' : existingDisp ? 'Update' : 'Record'}
                                            </button>
                                        </div>
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
};

export const MakerCheckerGovernancePanel = ({
    subjectId,
    subjectName,
    summary,
    watchlistHits = [],
    governanceState,
    activePersona,
    onChangePersona,
    onRefreshGovernance,
}) => {
    const [proposedRisk, setProposedRisk] = useState('Medium');
    const [proposedDecision, setProposedDecision] = useState('PROCEED_WITH_EDD_CONTROLS');
    const [makerRationale, setMakerRationale] = useState('');
    const [checkerRationale, setCheckerRationale] = useState('');
    const [itemVerdicts, setItemVerdicts] = useState({});
    const [itemRationales, setItemRationales] = useState({});
    const [savingItemId, setSavingItemId] = useState(null);
    const [submittingMaker, setSubmittingMaker] = useState(false);
    const [submittingChecker, setSubmittingChecker] = useState(false);
    const [bannerError, setBannerError] = useState(null);
    const [bannerSuccess, setBannerSuccess] = useState(null);
    const [showAuditLog, setShowAuditLog] = useState(true);

    const caseReview = governanceState?.case_review || { review_state: 'UNREVIEWED' };
    const dispositions = governanceState?.dispositions || {};
    const auditEvents = governanceState?.audit_events || [];
    const chainVerification = governanceState?.audit_chain_verification || { valid: true, event_count: 0 };
    const iapEmail = governanceState?.reviewer?.authenticated_email || 'admin@ramneekkhurana.altostrat.com';

    const reviewState = caseReview.review_state || 'UNREVIEWED';
    const isCaseLocked = reviewState === 'APPROVED';

    useEffect(() => {
        if (caseReview.proposed_risk_rating) {
            setProposedRisk(caseReview.proposed_risk_rating);
        } else if (summary?.risk_score && RISK_OPTIONS.includes(summary.risk_score)) {
            setProposedRisk(summary.risk_score);
        }
        if (caseReview.proposed_decision) {
            setProposedDecision(caseReview.proposed_decision);
        } else if ((watchlistHits || []).some(h => h.match_strength === 'CONFIRMED' || h.match_strength === 'STRONG')) {
            setProposedDecision('ESCALATE_TO_MLRO');
        }
        if (caseReview.maker_rationale) {
            setMakerRationale(caseReview.maker_rationale);
        }
    }, [caseReview.proposed_risk_rating, caseReview.proposed_decision, caseReview.maker_rationale, summary?.risk_score]);

    if (!subjectId) return null;

    const buildHeaders = (overrideEmail, overrideRole) => ({
        'Content-Type': 'application/json',
        'X-KYC-Acting-Reviewer': overrideEmail || activePersona.email,
        'X-KYC-Acting-Role': overrideRole || activePersona.role,
    });

    const handleSaveDisposition = async ({ item_id, item_type, item_title, verdict, rationale }) => {
        setBannerError(null);
        setBannerSuccess(null);
        setSavingItemId(item_id);
        try {
            const res = await fetch(`/api/subjects/${subjectId}/dispositions`, {
                method: 'POST',
                headers: buildHeaders(),
                body: JSON.stringify({ item_id, item_type, item_title, verdict, rationale }),
            });
            const data = await res.json();
            if (!res.ok) {
                setBannerError(data.error || 'Failed to record disposition.');
            } else {
                setBannerSuccess(`Recorded Maker disposition (${verdict}) on '${item_title}'.`);
                onRefreshGovernance && onRefreshGovernance(data);
            }
        } catch (err) {
            setBannerError(String(err));
        } finally {
            setSavingItemId(null);
        }
    };

    const handleMakerSubmit = async (e) => {
        e.preventDefault();
        setBannerError(null);
        setBannerSuccess(null);
        setSubmittingMaker(true);
        try {
            const res = await fetch(`/api/subjects/${subjectId}/review/submit`, {
                method: 'POST',
                headers: buildHeaders(),
                body: JSON.stringify({
                    proposed_risk_rating: proposedRisk,
                    proposed_decision: proposedDecision,
                    maker_rationale: makerRationale,
                }),
            });
            const data = await res.json();
            if (!res.ok) {
                setBannerError(data.error || 'Maker submission failed policy validation.');
            } else {
                setBannerSuccess(
                    `Case submitted for Checker sign-off by Maker (${activePersona.email}). Now awaiting independent L2 Checker approval.`
                );
                onRefreshGovernance && onRefreshGovernance(data);
            }
        } catch (err) {
            setBannerError(String(err));
        } finally {
            setSubmittingMaker(false);
        }
    };

    const handleCheckerDecision = async (action, forceSameIdentity = false) => {
        setBannerError(null);
        setBannerSuccess(null);
        setSubmittingChecker(true);
        try {
            const actingEmail = forceSameIdentity
                ? (caseReview.maker_email || iapEmail)
                : activePersona.email;
            const res = await fetch(`/api/subjects/${subjectId}/review/decide`, {
                method: 'POST',
                headers: buildHeaders(actingEmail, 'CHECKER'),
                body: JSON.stringify({
                    action,
                    checker_rationale:
                        checkerRationale.trim() ||
                        (forceSameIdentity
                            ? 'Attempting self-approval with same Maker identity to verify four-eyes block.'
                            : ''),
                }),
            });
            const data = await res.json();
            if (!res.ok) {
                setBannerError(data.error || 'Checker decision rejected.');
                if (data.audit_events && onRefreshGovernance) {
                    onRefreshGovernance(data);
                }
            } else {
                setBannerSuccess(
                    `Four-eyes review completed: Checker (${actingEmail}) marked case ${data.case_review?.review_state}.`
                );
                onRefreshGovernance && onRefreshGovernance(data);
            }
        } catch (err) {
            setBannerError(String(err));
        } finally {
            setSubmittingChecker(false);
        }
    };

    const humanReviewItems = (summary?.requires_human_review || []).filter(
        item => item.item_type !== 'WATCHLIST_HIT'
    );

    return (
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-sm space-y-6">
            {/* Header & Four-Eyes Identity Bar */}
            <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4 border-b border-slate-800 pb-4">
                <div className="space-y-1">
                    <div className="flex items-center gap-2.5 flex-wrap">
                        <UserCheck size={20} className="text-blue-400" />
                        <h2 className="text-base font-bold text-slate-100">
                            Four-Eyes (Maker-Checker) Governance & Immutable Audit Trail
                        </h2>
                        <span className={`px-2.5 py-0.5 text-[11px] font-bold rounded-full border ${stateBadge(reviewState)}`}>
                            {reviewState}
                        </span>
                        {isCaseLocked && (
                            <span className="inline-flex items-center gap-1 text-[11px] text-emerald-300 bg-emerald-950/60 px-2 py-0.5 rounded border border-emerald-800">
                                <Lock size={11} /> Case Signed Off & Locked
                            </span>
                        )}
                    </div>
                    <p className="text-xs text-slate-400">
                        Authenticated IAP Principal: <span className="font-mono text-slate-200">{iapEmail}</span>
                        {' '}· Separation of Duties Enforced (<code className="text-amber-300">maker_email != checker_email</code>)
                    </p>
                </div>

                {/* Role Switcher for Live Demonstration */}
                <div className="flex items-center gap-2 bg-slate-950 p-2 rounded-lg border border-slate-800">
                    <Users size={14} className="text-slate-400 ml-1" />
                    <span className="text-[11px] font-semibold text-slate-400">Active Signatory:</span>
                    <select
                        value={activePersona.key}
                        onChange={(e) => onChangePersona(e.target.value)}
                        className="bg-slate-900 border border-slate-700 rounded px-2.5 py-1 text-xs font-semibold text-slate-100 focus:outline-none focus:border-blue-500"
                    >
                        <option value="maker">
                            L1 Maker — {iapEmail} (IAP Principal)
                        </option>
                        <option value="checker">
                            L2 Checker / MLRO — compliance.checker@ramneekkhurana.altostrat.com
                        </option>
                    </select>
                </div>
            </div>

            {/* Feedback Alerts */}
            {bannerError && (
                <div className="p-3.5 rounded-xl bg-red-950/50 border border-red-700 text-red-200 text-xs flex items-start gap-2.5">
                    <AlertTriangle size={16} className="text-red-400 shrink-0 mt-0.5" />
                    <div className="flex-1">
                        <span className="font-bold uppercase tracking-wider block mb-0.5">
                            Governance / Separation-of-Duties Control Triggered
                        </span>
                        {bannerError}
                    </div>
                </div>
            )}
            {bannerSuccess && (
                <div className="p-3.5 rounded-xl bg-emerald-950/50 border border-emerald-700 text-emerald-200 text-xs flex items-start gap-2.5">
                    <CheckCircle size={16} className="text-emerald-400 shrink-0 mt-0.5" />
                    <div className="flex-1">{bannerSuccess}</div>
                </div>
            )}

            {/* Per-Item Adverse Media Human-Review Dispositions */}
            {humanReviewItems.length > 0 && (
                <div className="space-y-3">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-amber-300 flex items-center gap-1.5">
                        <FileCheck size={14} className="text-amber-400" />
                        Adverse Media Human-Review Queue ({humanReviewItems.length}) — Maker Dispositions
                    </h3>
                    <div className="space-y-2.5">
                        {humanReviewItems.map((item, idx) => {
                            const itemId = item.finding_id || `review-item-${idx}`;
                            const existingDisp = dispositions[itemId];
                            const currentVerdict = itemVerdicts[itemId] || existingDisp?.verdict || 'MITIGATED_ACCEPTABLE';
                            const currentRationale = itemRationales[itemId] ?? existingDisp?.rationale ?? '';

                            return (
                                <div key={itemId} className="p-3.5 rounded-lg bg-slate-950/70 border border-slate-800 space-y-2.5">
                                    <div className="flex items-start justify-between gap-2">
                                        <div>
                                            <div className="flex items-center gap-2 flex-wrap">
                                                <span className="text-xs font-bold text-slate-200">
                                                    {item.title || item.reason}
                                                </span>
                                                <span className="text-[10px] px-2 py-0.5 rounded bg-amber-950/60 text-amber-300 border border-amber-800">
                                                    {item.reason}
                                                </span>
                                                {existingDisp ? (
                                                    <span className={`text-[10px] px-2 py-0.5 rounded border font-semibold ${verdictBadge(existingDisp.verdict)}`}>
                                                        Dispositioned: {existingDisp.verdict} (by {existingDisp.maker_email})
                                                    </span>
                                                ) : (
                                                    <span className="text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">
                                                        Pending Maker Disposition
                                                    </span>
                                                )}
                                            </div>
                                            <p className="text-xs text-slate-400 mt-1">{item.detail}</p>
                                        </div>
                                    </div>

                                    <div className="grid grid-cols-1 md:grid-cols-12 gap-2 items-end pt-2 border-t border-slate-800/70">
                                        <div className="md:col-span-4">
                                            <select
                                                disabled={isCaseLocked}
                                                value={currentVerdict}
                                                onChange={(e) => setItemVerdicts(prev => ({ ...prev, [itemId]: e.target.value }))}
                                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-200"
                                            >
                                                {DISPOSITION_OPTIONS.map(opt => (
                                                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                                                ))}
                                            </select>
                                        </div>
                                        <div className="md:col-span-6">
                                            <input
                                                type="text"
                                                disabled={isCaseLocked}
                                                placeholder="Maker assessment rationale (min 10 chars)..."
                                                value={currentRationale}
                                                onChange={(e) => setItemRationales(prev => ({ ...prev, [itemId]: e.target.value }))}
                                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-200"
                                            />
                                        </div>
                                        <div className="md:col-span-2">
                                            <button
                                                type="button"
                                                disabled={isCaseLocked || savingItemId === itemId}
                                                onClick={() =>
                                                    handleSaveDisposition({
                                                        item_id: itemId,
                                                        item_type: 'FINDING',
                                                        item_title: item.title || item.reason,
                                                        verdict: currentVerdict,
                                                        rationale: currentRationale,
                                                    })
                                                }
                                                className="w-full px-3 py-1.5 rounded-lg text-xs font-semibold bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50"
                                            >
                                                {savingItemId === itemId ? 'Saving...' : existingDisp ? 'Update' : 'Save'}
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}

            {/* Two-Column Maker & Checker Workflow Cards */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                {/* Step 1: Maker Submission */}
                <form
                    onSubmit={handleMakerSubmit}
                    className="p-4 rounded-xl bg-slate-950/80 border border-slate-800 space-y-3.5"
                >
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2.5">
                        <div>
                            <span className="text-[10px] font-bold uppercase tracking-wider text-blue-400">
                                Step 1 · First Line of Defence (L1 Maker)
                            </span>
                            <h3 className="text-sm font-bold text-slate-100">
                                Maker Risk Rating & CDD Recommendation
                            </h3>
                        </div>
                        {caseReview.maker_email && (
                            <span className="text-[11px] text-slate-400 font-mono">
                                Signed: {caseReview.maker_email}
                            </span>
                        )}
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div>
                            <label className="block text-[10px] font-semibold text-slate-400 uppercase mb-1">
                                Proposed Customer Risk Rating
                            </label>
                            <select
                                disabled={isCaseLocked}
                                value={proposedRisk}
                                onChange={(e) => setProposedRisk(e.target.value)}
                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-2 text-xs text-slate-100"
                            >
                                {RISK_OPTIONS.map(r => (
                                    <option key={r} value={r}>{r} Risk</option>
                                ))}
                            </select>
                        </div>
                        <div>
                            <label className="block text-[10px] font-semibold text-slate-400 uppercase mb-1">
                                Proposed Relationship Decision
                            </label>
                            <select
                                disabled={isCaseLocked}
                                value={proposedDecision}
                                onChange={(e) => setProposedDecision(e.target.value)}
                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-2 text-xs text-slate-100"
                            >
                                {DECISION_OPTIONS.map(opt => (
                                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                                ))}
                            </select>
                        </div>
                    </div>

                    <div>
                        <label className="block text-[10px] font-semibold text-slate-400 uppercase mb-1">
                            Maker Written Justification & Mitigants (Mandatory)
                        </label>
                        <textarea
                            rows={3}
                            disabled={isCaseLocked}
                            placeholder="Summarize corroborating ID evidence, disposition of watchlist/adverse-media hits, and SOW verification..."
                            value={makerRationale}
                            onChange={(e) => setMakerRationale(e.target.value)}
                            className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-xs text-slate-200 focus:outline-none focus:border-blue-500"
                        />
                    </div>

                    <div className="flex items-center justify-between pt-1">
                        <span className="text-[11px] text-slate-400">
                            Submitting as: <strong className="text-slate-200 font-mono">{activePersona.email}</strong>
                        </span>
                        <button
                            type="submit"
                            disabled={isCaseLocked || submittingMaker}
                            className="px-4 py-2 rounded-lg text-xs font-bold bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50"
                        >
                            {submittingMaker
                                ? 'Submitting...'
                                : reviewState === 'PENDING_CHECKER_APPROVAL'
                                    ? 'Update Maker Submission'
                                    : 'Submit for Checker Sign-Off'}
                        </button>
                    </div>
                </form>

                {/* Step 2: Checker Sign-Off */}
                <div className="p-4 rounded-xl bg-slate-950/80 border border-slate-800 space-y-3.5 flex flex-col justify-between">
                    <div className="space-y-3">
                        <div className="flex items-center justify-between border-b border-slate-800 pb-2.5">
                            <div>
                                <span className="text-[10px] font-bold uppercase tracking-wider text-emerald-400">
                                    Step 2 · Second Line of Defence (L2 Checker / MLRO)
                                </span>
                                <h3 className="text-sm font-bold text-slate-100">
                                    Independent Four-Eyes Sign-Off
                                </h3>
                            </div>
                            {caseReview.checker_email && (
                                <span className="text-[11px] text-emerald-300 font-mono">
                                    Checked: {caseReview.checker_email}
                                </span>
                            )}
                        </div>

                        {caseReview.maker_email ? (
                            <div className="p-3 rounded-lg bg-slate-900 border border-slate-800 text-xs space-y-1">
                                <div className="flex justify-between">
                                    <span className="text-slate-400">Maker Proposal:</span>
                                    <span className="font-bold text-slate-100">
                                        {caseReview.proposed_risk_rating} Risk · {caseReview.proposed_decision}
                                    </span>
                                </div>
                                <div className="flex justify-between">
                                    <span className="text-slate-400">Submitted By (Maker):</span>
                                    <span className="font-mono text-amber-300">{caseReview.maker_email}</span>
                                </div>
                                <p className="text-slate-300 italic pt-1 border-t border-slate-800/80 mt-1">
                                    "{caseReview.maker_rationale}"
                                </p>
                            </div>
                        ) : (
                            <div className="p-4 rounded-lg bg-slate-900/50 border border-slate-800 text-xs text-slate-400 text-center">
                                Awaiting L1 Maker submission before Checker sign-off can be performed.
                            </div>
                        )}

                        <div>
                            <label className="block text-[10px] font-semibold text-slate-400 uppercase mb-1">
                                Checker Review Commentary & Conditions (Mandatory)
                            </label>
                            <textarea
                                rows={2}
                                disabled={reviewState !== 'PENDING_CHECKER_APPROVAL'}
                                placeholder="Record independent L2 concurrence, EDD conditions, or reasons for returning to Maker..."
                                value={checkerRationale}
                                onChange={(e) => setCheckerRationale(e.target.value)}
                                className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-xs text-slate-200 focus:outline-none focus:border-blue-500 disabled:opacity-50"
                            />
                        </div>
                    </div>

                    <div className="space-y-2 pt-1">
                        <div className="flex flex-wrap gap-2">
                            <button
                                type="button"
                                disabled={reviewState !== 'PENDING_CHECKER_APPROVAL' || submittingChecker}
                                onClick={() => handleCheckerDecision('APPROVE', false)}
                                className="flex-1 px-3 py-2 rounded-lg text-xs font-bold bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-40"
                            >
                                Approve & Lock Case
                            </button>
                            <button
                                type="button"
                                disabled={reviewState !== 'PENDING_CHECKER_APPROVAL' || submittingChecker}
                                onClick={() => handleCheckerDecision('RETURN', false)}
                                className="px-3 py-2 rounded-lg text-xs font-bold bg-amber-600 hover:bg-amber-500 text-white disabled:opacity-40"
                            >
                                Return to Maker
                            </button>
                            <button
                                type="button"
                                disabled={reviewState !== 'PENDING_CHECKER_APPROVAL' || submittingChecker}
                                onClick={() => handleCheckerDecision('ESCALATE', false)}
                                className="px-3 py-2 rounded-lg text-xs font-bold bg-purple-600 hover:bg-purple-500 text-white disabled:opacity-40"
                            >
                                Escalate to MLRO
                            </button>
                        </div>

                        {reviewState === 'PENDING_CHECKER_APPROVAL' && (
                            <div className="flex items-center justify-between text-[11px] text-slate-400 pt-1">
                                <span>
                                    Checking as: <strong className="text-slate-200 font-mono">{activePersona.email}</strong>
                                </span>
                                <button
                                    type="button"
                                    onClick={() => handleCheckerDecision('APPROVE', true)}
                                    className="text-amber-400 hover:text-amber-300 underline font-semibold"
                                    title="Attempts to approve using the Maker's own email to demonstrate the HTTP 409 separation-of-duties block and audit event."
                                >
                                    Test Self-Approval Block (Same Maker & Checker)
                                </button>
                            </div>
                        )}
                    </div>
                </div>
            </div>

            {/* Tamper-Evident SHA-256 Hash-Chained Audit Trail */}
            <div className="border-t border-slate-800 pt-4">
                <div className="flex items-center justify-between">
                    <button
                        type="button"
                        onClick={() => setShowAuditLog(prev => !prev)}
                        className="flex items-center gap-2 text-xs font-bold text-slate-200 uppercase tracking-wider hover:text-blue-400"
                    >
                        <History size={15} className="text-blue-400" />
                        Immutable SHA-256 Hash-Chained Audit Trail ({auditEvents.length} Events)
                        {showAuditLog ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    </button>

                    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-mono font-semibold border ${
                        chainVerification.valid
                            ? 'bg-emerald-950/60 text-emerald-300 border-emerald-800'
                            : 'bg-red-950/60 text-red-300 border-red-700'
                    }`}>
                        <CheckCircle size={12} />
                        {chainVerification.valid
                            ? `Hash Chain Verified (${chainVerification.event_count || auditEvents.length} links · head #${(chainVerification.head_hash || '00000000').slice(0, 10)})`
                            : `TAMPER DETECTED: ${chainVerification.reason}`}
                    </span>
                </div>

                {showAuditLog && (
                    <div className="mt-3 overflow-x-auto rounded-lg border border-slate-800 bg-slate-950/70">
                        {auditEvents.length === 0 ? (
                            <p className="p-4 text-xs text-slate-500 text-center">
                                No governance events recorded for this subject yet.
                            </p>
                        ) : (
                            <table className="w-full text-left border-collapse text-xs">
                                <thead>
                                    <tr className="border-b border-slate-800 text-[10px] uppercase text-slate-400 bg-slate-900/60">
                                        <th className="py-2 px-3">Timestamp (UTC)</th>
                                        <th className="py-2 px-3">Event Type</th>
                                        <th className="py-2 px-3">Actor & Auth</th>
                                        <th className="py-2 px-3">Transition</th>
                                        <th className="py-2 px-3">Details / Rationale</th>
                                        <th className="py-2 px-3 font-mono">SHA-256 Chain</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-slate-800/70">
                                    {[...auditEvents].reverse().map((ev) => {
                                        const p = ev.payload || {};
                                        const detail =
                                            p.rationale ||
                                            p.maker_rationale ||
                                            p.checker_rationale ||
                                            p.reason ||
                                            p.coverage ||
                                            JSON.stringify(p);
                                        return (
                                            <tr key={ev.event_id} className="hover:bg-slate-900/40">
                                                <td className="py-2 px-3 font-mono text-[11px] text-slate-400 whitespace-nowrap">
                                                    {(ev.created_at || '').replace('T', ' ').slice(0, 19)}
                                                </td>
                                                <td className="py-2 px-3 font-semibold text-slate-200 whitespace-nowrap">
                                                    <span className={`px-2 py-0.5 rounded text-[10px] font-mono ${
                                                        ev.event_type.includes('VIOLATION')
                                                            ? 'bg-red-950 text-red-300 border border-red-800'
                                                            : ev.event_type.includes('CHECKER')
                                                                ? 'bg-emerald-950 text-emerald-300 border border-emerald-800'
                                                                : 'bg-slate-900 text-blue-300 border border-slate-700'
                                                    }`}>
                                                        {ev.event_type}
                                                    </span>
                                                </td>
                                                <td className="py-2 px-3 font-mono text-[11px] text-slate-300">
                                                    {ev.actor_email}
                                                    <span className="block text-[10px] text-slate-500">
                                                        {ev.actor_auth_source}
                                                    </span>
                                                </td>
                                                <td className="py-2 px-3 text-[11px] text-slate-400 whitespace-nowrap">
                                                    {ev.previous_state ? `${ev.previous_state} → ` : ''}
                                                    <strong className="text-slate-200">{ev.new_state || '—'}</strong>
                                                </td>
                                                <td className="py-2 px-3 text-slate-300 max-w-md truncate" title={detail}>
                                                    {detail}
                                                </td>
                                                <td className="py-2 px-3 font-mono text-[10px] text-slate-400 whitespace-nowrap">
                                                    #{(ev.prev_event_hash || '').slice(0, 6)} →{' '}
                                                    <span className="text-emerald-400">#{(ev.event_hash || '').slice(0, 8)}</span>
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};

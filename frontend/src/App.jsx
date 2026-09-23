/*
Copyright 2025 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

import React, { useState, useEffect, useRef, useMemo } from 'react';
import { Settings, ExternalLink, AlertTriangle, CheckCircle, Network, Download, ArrowUp, ArrowDown, Filter, Star, X, User, UserCheck, ShieldAlert, HelpCircle } from 'lucide-react';
import GraphView from './GraphView';
import ErrorBoundary from './ErrorBoundary';
import ReactMarkdown from 'react-markdown';

// New Components
import SearchArea from './components/SearchArea';
import IdentityPanel from './components/IdentityPanel';
import ProcessLog from './components/ProcessLog';
import DocumentChecker from './components/DocumentChecker';
import {
    WatchlistScreeningPanel,
    MakerCheckerGovernancePanel,
    LiveSystemStatusBar,
    CaseWorkspaceTabs,
} from './components/GovernanceAndWatchlist';

// --- Reusable Components (Keep mainly existing ones for consistency if needed by children) ---
const Spinner = ({ size = 'md' }) => {
    const sizeClasses = { sm: 'w-5 h-5', md: 'w-16 h-16', lg: 'w-24 h-24' };
    return (
        <div className="flex justify-center items-center">
            <div className={`${sizeClasses[size]} border-2 border-blue-200 border-t-blue-600 rounded-full animate-spin`}></div>
        </div>
    );
};

const CostDisplay = ({ stats }) => {
    if (!stats) return null;
    return (
        <div className="flex items-center gap-4 text-xs text-slate-400 bg-slate-900/50 px-3 py-1.5 rounded-full border border-slate-700 shadow-sm animate-fade-in">
            <div className="flex items-center gap-1" title="Estimated cost based on Gemini pricing">
                <span className="font-semibold text-slate-300">Est. Cost:</span>
                <span className="font-mono text-emerald-400">${stats.cost.toFixed(4)}</span>
            </div>
            <div className="w-px h-3 bg-slate-600"></div>
            <div className="flex items-center gap-1" title="Total tokens processed">
                <span className="font-semibold text-slate-300">Tokens:</span>
                <span className="font-mono">{Math.round(stats.input_tokens + stats.output_tokens).toLocaleString()}</span>
            </div>
        </div>
    );
};

const Card = ({ children, className = '' }) => <div className={`bg-slate-900 border border-slate-800 rounded-xl shadow-sm p-6 ${className}`}>{children}</div>;

const Button = ({ children, onClick, variant = 'primary', className = '', disabled = false }) => {
    const base = 'px-4 py-2 rounded-lg font-semibold text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2 flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed';
    const variants = { primary: 'bg-blue-600 text-white hover:bg-blue-700 focus:ring-blue-500', secondary: 'bg-gray-200 text-gray-800 hover:bg-gray-300 focus:ring-gray-400' };
    return <button onClick={onClick} className={`${base} ${variants[variant]} ${className}`} disabled={disabled}>{children}</button>;
};

const Dropdown = ({ label, options, value, onChange, className = '', icon }) => (
    <div className={className}>
        {label && <label className="flex items-center gap-2 text-sm font-medium text-gray-500 mb-1">{icon}{label}</label>}
        <div className="relative">
            <select value={value} onChange={e => onChange(e.target.value)} className="w-full bg-white border border-gray-300 rounded-md py-2 px-3 text-sm appearance-none focus:outline-none focus:ring-2 focus:ring-blue-500">
                {options.map(opt => <option key={opt.value || opt} value={opt.value || opt}>{opt.label || opt}</option>)}
            </select>
            <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400 pointer-events-none" />
        </div>
    </div>
);
const ChevronDown = ({ className }) => <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m6 9 6 6 6-6" /></svg>;

// --- Helper Badges ---
const MatchStatus = ({ status }) => {
    const config = {
        'Confirmed': { text: 'Confirmed', color: 'bg-green-100 text-green-800 border-green-200' },
        'Likely': { text: 'Likely', color: 'bg-blue-100 text-blue-800 border-blue-200' },
        'Possible': { text: 'Possible', color: 'bg-yellow-100 text-yellow-800 border-yellow-200' },
        'Negative': { text: 'Negative', color: 'bg-gray-100 text-gray-500 border-gray-200' },
        'HITL': { text: 'Review Needed', color: 'bg-orange-100 text-orange-800 border-orange-200' }
    }[status] || { text: status || 'Unknown', color: 'bg-gray-100 text-gray-500 border-gray-200' };
    return <span className={`inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-full border ${config.color}`}>{config.text}</span>;
};

const ReputationBadge = ({ rep }) => {
    const config = {
        'Tier 1 News': { color: 'text-purple-700 bg-purple-50 border-purple-100' },
        'Regional News': { color: 'text-blue-700 bg-blue-50 border-blue-100' },
        'Industry Trade': { color: 'text-teal-700 bg-teal-50 border-teal-100' },
        'Blog/Opinion': { color: 'text-gray-600 bg-gray-50 border-gray-200' },
        'Social Media': { color: 'text-pink-700 bg-pink-50 border-pink-100' }
    }[rep] || { color: 'text-gray-500 bg-gray-50' };
    return <span className={`px-2 py-0.5 rounded text-xs border ${config.color}`}>{rep}</span>;
};

const RiskIndicator = ({ level }) => {
    const config = {
        'Critical': { text: 'Critical Sanctions Risk', icon: <ShieldAlert size={16} />, color: 'bg-red-900 text-red-100 border-red-500' },
        'High': { text: 'High Risk', icon: <AlertTriangle size={16} />, color: 'bg-red-100 text-red-800 border-red-200' },
        'Medium': { text: 'Medium Risk', icon: <AlertTriangle size={16} />, color: 'bg-yellow-100 text-yellow-800 border-yellow-200' },
        'Low': { text: 'Low Risk', icon: <CheckCircle size={16} />, color: 'bg-blue-100 text-blue-800 border-blue-200' },
        'None': { text: 'No Risk Found', icon: <CheckCircle size={16} />, color: 'bg-green-100 text-green-800 border-green-200' },
        'Unknown': { text: 'Unknown', icon: <User size={16} />, color: 'bg-gray-100 text-gray-800 border-gray-200' },
        'Uncategorized': { text: 'Uncategorized', icon: <User size={16} />, color: 'bg-gray-100 text-gray-800 border-gray-200' }
    }[level] || { text: 'N/A', icon: <User size={16} />, color: 'bg-gray-100 text-gray-800 border-gray-200' };
    return <span className={`inline-flex items-center gap-2 px-3 py-1 text-xs font-semibold rounded-full border ${config.color}`}>{config.icon} {config.text}</span>;
};

const RelevanceScore = ({ score }) => {
    let colorClass = 'text-slate-400';
    if (score >= 8) colorClass = 'text-emerald-400';
    else if (score >= 5) colorClass = 'text-amber-400';

    return (
        <div className={`font-bold text-sm ${colorClass}`}>
            {score.toFixed(1)}/10
        </div>
    );
};



const Pagination = ({ currentPage, totalPages, onPageChange }) => {
    if (totalPages <= 1) return null;
    return (
        <div className="flex justify-center items-center gap-2 mt-4 text-slate-400">
            <Button variant="secondary" onClick={() => onPageChange(currentPage - 1)} disabled={currentPage === 1} className="!px-2 !py-1 text-xs bg-slate-800 text-slate-300 border border-slate-700 hover:bg-slate-700">Previous</Button>
            <span className="text-sm font-medium">Page {currentPage} of {totalPages}</span>
            <Button variant="secondary" onClick={() => onPageChange(currentPage + 1)} disabled={currentPage === totalPages} className="!px-2 !py-1 text-xs bg-slate-800 text-slate-300 border border-slate-700 hover:bg-slate-700">Next</Button>
        </div>
    );
};

const ResultsTable = ({ results, riskCategoryFilter, onFilterChange, appConfig, searchId, subjectName, setCostStats, loadedGraphData }) => {
    const [currentPage, setCurrentPage] = useState(1);
    const [sortConfig, setSortConfig] = useState({ key: 'relevance_score', direction: 'desc' });
    const [downloading, setDownloading] = useState({ active: false, id: null });
    const [activeTab, setActiveTab] = useState('list'); // 'list' or 'graph'
    const [graphData, setGraphData] = useState(null);
    const [isGeneratingGraph, setIsGeneratingGraph] = useState(false);
    const rowsPerPage = 50;

    useEffect(() => {
        if (loadedGraphData) {
            setGraphData(loadedGraphData);
        }
    }, [loadedGraphData]);

    const filteredResults = useMemo(() => {
        if (riskCategoryFilter === 'All') return results;
        return results.filter(item => item.risk_category === riskCategoryFilter);
    }, [results, riskCategoryFilter]);

    const sortedResults = useMemo(() => {
        const priorityResults = filteredResults.filter(item => item.source_type === 'priority_search');
        const geminiResults = filteredResults.filter(item => item.source_type !== 'priority_search');

        const sortList = (list) => {
            let sortableItems = [...list];
            if (sortConfig.key) {
                sortableItems.sort((a, b) => {
                    if (a[sortConfig.key] < b[sortConfig.key]) return sortConfig.direction === 'asc' ? -1 : 1;
                    if (a[sortConfig.key] > b[sortConfig.key]) return sortConfig.direction === 'asc' ? 1 : -1;
                    return 0;
                });
            }
            return sortableItems;
        };

        return [...sortList(priorityResults), ...sortList(geminiResults)];
    }, [filteredResults, sortConfig]);

    useEffect(() => {
        setGraphData(null);
        setActiveTab('list');
    }, [searchId]);

    const totalPages = Math.ceil(sortedResults.length / rowsPerPage);
    const paginatedResults = sortedResults.slice((currentPage - 1) * rowsPerPage, currentPage * rowsPerPage);

    const requestSort = (key) => {
        let direction = 'asc';
        if (sortConfig.key === key && sortConfig.direction === 'asc') {
            direction = 'desc';
        }
        setSortConfig({ key, direction });
    };

    const getSortIcon = (key) => {
        if (sortConfig.key !== key) return null;
        return sortConfig.direction === 'asc' ? <ArrowUp size={14} className="ml-1" /> : <ArrowDown size={14} className="ml-1" />;
    };

    // Helper function to call backend for scraping and download
    const fetchAndDownload = async (resultsToExport, baseFilename) => {
        try {
            const response = await fetch('/api/export-findings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    subjectName: subjectName,
                    results: resultsToExport
                })
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || `Server responded with status: ${response.status}`);
            }

            const processedData = await response.json();

            // Download logic
            const jsonStr = JSON.stringify(processedData, null, 2);
            const blob = new Blob([jsonStr], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${baseFilename}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);

        } catch (error) {
            console.error("Failed to export findings:", error);
            alert(`Failed to export findings: ${error.message}`);
        }
    };

    const handleDownloadAll = async () => {
        setDownloading({ active: true, id: 'all' });
        const filename = `all_findings_for_${subjectName.replace(/\s+/g, '_')}`;
        await fetchAndDownload(results, filename);
        setDownloading({ active: false, id: null });
    };

    const handleDownloadSingle = async (item, index) => {
        setDownloading({ active: true, id: index });
        const safePart = item.url ? item.url.split('/').pop().replace(/[^a-zA-Z0-9]/g, '_').slice(0, 20) : 'no_url';
        const filename = `finding_for_${subjectName.replace(/\s+/g, '_')}_${safePart}`;
        await fetchAndDownload([item], filename);
        setDownloading({ active: false, id: null });
    };

    if (results.length === 0) return null;

    return (
        <Card className="max-w-full mx-auto !p-0 overflow-hidden min-h-[500px] flex flex-col bg-slate-900 border border-slate-800 shadow-sm">
            <div className="flex justify-between items-center p-4 border-b border-slate-800 bg-slate-900 relative z-10">
                <div className="flex items-center gap-6">
                    <h2 className="text-lg font-bold text-slate-100">Findings ({filteredResults.length})</h2>
                    <div className="flex bg-slate-800 p-1 rounded-lg border border-slate-700">
                        <button onClick={() => setActiveTab('list')} className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-all ${activeTab === 'list' ? 'bg-slate-700 text-blue-400 shadow-sm' : 'text-slate-400 hover:text-slate-200'}`}>Feed</button>
                        <button onClick={() => setActiveTab('graph')} className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-all flex items-center gap-2 ${activeTab === 'graph' ? 'bg-slate-700 text-blue-400 shadow-sm' : 'text-slate-400 hover:text-slate-200'}`}><Network size={14} /> Entity Graph</button>
                    </div>
                </div>

                <div className="flex items-center gap-4">
                    {activeTab === 'list' && (
                        <>
                            <div className="flex items-center gap-2 mr-4">
                                <span className="text-[10px] uppercase font-bold text-slate-500 tracking-wider">Sort By:</span>
                                <button onClick={() => requestSort('relevance_score')} className={`text-xs px-2 py-1 rounded border border-slate-700 ${sortConfig.key === 'relevance_score' ? 'bg-slate-800 text-emerald-400' : 'text-slate-400 hover:bg-slate-800'}`}>
                                    Relevance
                                </button>
                                <button onClick={() => requestSort('risk_level')} className={`text-xs px-2 py-1 rounded border border-slate-700 ${sortConfig.key === 'risk_level' ? 'bg-slate-800 text-red-400' : 'text-slate-400 hover:bg-slate-800'}`}>
                                    Risk
                                </button>
                            </div>

                            <Button onClick={handleDownloadAll} variant="primary" disabled={results.length === 0 || downloading.active} className="!text-xs bg-blue-600 text-white border border-blue-500 hover:bg-blue-500">
                                {downloading.active && downloading.id === 'all' ? <Spinner size="sm" /> : <Download size={14} />}
                                {downloading.active && downloading.id === 'all' ? 'Preparing...' : `Download All`}
                            </Button>
                            <Dropdown
                                label=""
                                icon={<Filter size={14} className="text-slate-400" />}
                                options={["All", ...(appConfig?.RISK_CATEGORY_OPTIONS || [])].map(c => ({ value: c, label: c }))}
                                value={riskCategoryFilter}
                                onChange={onFilterChange}
                                className="min-w-[150px] bg-slate-950 text-slate-300 border-slate-700"
                            />
                        </>
                    )}
                </div>
            </div>

            <div className="flex-grow bg-slate-900">
                {activeTab === 'list' ? (
                    <div className="h-full overflow-y-auto scrollbar-thin scrollbar-thumb-slate-700 scrollbar-track-slate-900 p-4 space-y-4">
                        {paginatedResults.map((item, index) => (
                            <div key={index} className="flex flex-col md:flex-row gap-4 p-4 rounded-xl bg-slate-800/50 border border-slate-800 hover:border-slate-700 hover:shadow-lg transition-all group">
                                {/* Left: Source & Identity */}
                                <div className="md:w-1/5 min-w-[180px] shrink-0 flex flex-col gap-2 border-b md:border-b-0 md:border-r border-slate-700/50 pb-3 md:pb-0 md:pr-4">
                                    <div className="flex items-center justify-between">
                                        {item.source_type === 'priority_search' && <span className="text-[9px] font-bold text-blue-300 bg-blue-900/40 px-1.5 py-0.5 rounded border border-blue-800/50 uppercase tracking-wide">Priority</span>}
                                        <div className="text-[10px] text-slate-500 font-mono">{item.source_date !== 'N/A' ? item.source_date : ''}</div>
                                    </div>

                                    <a href={item.url} target="_blank" rel="noopener noreferrer" className="text-sm font-bold text-blue-400 group-hover:text-blue-300 transition-colors break-words line-clamp-1">
                                        {item.media_house || "Unknown Source"}
                                    </a>

                                    <div className="mt-1">
                                        <ReputationBadge rep={item.media_house_reputation} />
                                    </div>
                                    <div className="mt-auto pt-2">
                                        <MatchStatus status={item.match_status} />
                                    </div>
                                </div>

                                {/* Middle: Insight */}
                                <div className="flex-1 min-w-0 flex flex-col gap-1">
                                    <h3 className="text-sm font-semibold text-slate-200 line-clamp-2 leading-snug group-hover:text-white transition-colors">
                                        {item.source_title}
                                    </h3>
                                    <p className="text-xs text-slate-400 leading-relaxed line-clamp-3 mt-1">
                                        {item.gemini_insight}
                                    </p>
                                    <div className="mt-2 text-[10px] text-slate-500">
                                        <span className="font-semibold text-slate-400">Category:</span> {item.risk_category}
                                    </div>
                                </div>

                                {/* Right: Vitals */}
                                <div className="md:w-[120px] shrink-0 flex flex-row md:flex-col items-center justify-between md:justify-center gap-3 border-t md:border-t-0 md:border-l border-slate-700/50 pt-3 md:pt-0 md:pl-4">
                                    <RiskIndicator level={item.risk_level} />

                                    <div className="flex flex-col items-center">
                                        <div className="text-[10px] uppercase font-bold text-slate-600 mb-0.5 tracking-wider">Relevance</div>
                                        <RelevanceScore score={item.relevance_score} />
                                    </div>
                                </div>
                            </div>
                        ))}

                        <div className="pt-4 border-t border-slate-800">
                            <Pagination currentPage={currentPage} totalPages={totalPages} onPageChange={setCurrentPage} />
                        </div>
                    </div>
                ) : (
                    <div className="w-full h-[500px] flex items-center justify-center p-4 bg-slate-900">
                        {!graphData ? (
                            <div className="text-center max-w-md">
                                <Network size={48} className="mx-auto text-slate-600 mb-4" />
                                <h3 className="text-lg font-bold text-slate-300 mb-2">Entity Knowledge Graph</h3>
                                <p className="text-slate-500 text-sm">Tracing relationships...</p>
                            </div>
                        ) : (
                            <ErrorBoundary>
                                <GraphView nodes={graphData.nodes} edges={graphData.edges} subjectName={subjectName} />
                            </ErrorBoundary>
                        )}
                    </div>
                )}
            </div>
        </Card >
    );
};

// --- Screening outcome vocabulary -------------------------------------------
// These MUST mirror backend/search/screening.py. The system deliberately never
// says "Accept" or "Reject": an automated adverse-media search is one input to
// a CDD decision, not the decision itself, and rendering it as one invites a
// reviewer to rubber-stamp it.
const RECOMMENDATION_NO_ADVERSE_MEDIA = 'No Adverse Media Found';
const RECOMMENDATION_REQUIRES_REVIEW = 'Adverse Media - Requires Review';
const RECOMMENDATION_ESCALATE = 'Adverse Media - Escalate';
const RECOMMENDATION_SANCTIONS_HIT = 'Sanctions / Watchlist Match - Escalate to Compliance / MLRO';
const RECOMMENDATION_INCOMPLETE = 'Screening Incomplete - Manual Review Required';

const recommendationStyle = (recommendation) => {
    switch (recommendation) {
        case RECOMMENDATION_NO_ADVERSE_MEDIA:
            return 'bg-emerald-900/30 text-emerald-300 border-emerald-800';
        case RECOMMENDATION_SANCTIONS_HIT:
            return 'bg-red-950 text-red-200 border-red-500 shadow-sm';
        case RECOMMENDATION_ESCALATE:
            return 'bg-red-900/30 text-red-300 border-red-800';
        case RECOMMENDATION_INCOMPLETE:
            // Grey, not green. An incomplete screen is an absence of evidence,
            // and must never be styled like a clean result.
            return 'bg-slate-800 text-slate-300 border-slate-600';
        case RECOMMENDATION_REQUIRES_REVIEW:
        default:
            return 'bg-amber-900/30 text-amber-300 border-amber-800';
    }
};

const RecommendationIcon = ({ recommendation }) => {
    switch (recommendation) {
        case RECOMMENDATION_NO_ADVERSE_MEDIA:
            return <CheckCircle size={14} />;
        case RECOMMENDATION_SANCTIONS_HIT:
        case RECOMMENDATION_ESCALATE:
            return <ShieldAlert size={14} />;
        case RECOMMENDATION_INCOMPLETE:
            return <HelpCircle size={14} />;
        default:
            return <AlertTriangle size={14} />;
    }
};

const SummaryReport = ({ summary, onOpenGovernance }) => {
    const [expandReviewQueue, setExpandReviewQueue] = useState(false);

    if (!summary) return null;

    if (typeof summary === 'string' || (summary.summary_text && !summary.risk_score)) {
        return (
            <Card className="!bg-slate-900 border-l-4 border-blue-500 border border-t-slate-800 border-r-slate-800 border-b-slate-800">
                <div className="flex items-center gap-2 mb-4">
                    <h2 className="text-lg font-bold text-slate-100">Executive Summary</h2>
                    <span className="text-[10px] bg-slate-800 text-slate-400 px-2 py-1 rounded border border-slate-700">Legacy Format</span>
                </div>
                <div className="prose prose-sm prose-invert max-w-none text-slate-300">
                    <ReactMarkdown>{summary.summary_text || summary}</ReactMarkdown>
                </div>
            </Card>
        );
    }

    const reviewItems = summary.requires_human_review || [];
    const visibleReviewItems = expandReviewQueue ? reviewItems : reviewItems.slice(0, 4);

    return (
        <Card className="space-y-6 !bg-slate-900 border border-slate-800 shadow-sm relative overflow-hidden">
            {/* Decorative Background for High Risk */}
            {(summary.risk_score === 'High' || summary.risk_score === 'Critical') && (
                <div className="absolute top-0 right-0 w-32 h-32 bg-red-500/10 rounded-bl-full pointer-events-none"></div>
            )}

            <div className="flex flex-col md:flex-row md:justify-between md:items-start gap-4 border-b border-slate-800 pb-4 relative z-10">
                <div className="space-y-1 flex-1">
                    <h2 className="text-lg font-bold text-slate-100">Executive Risk Summary</h2>
                    <p className="text-sm font-medium text-slate-400 italic max-w-2xl">"{summary.reasoning}"</p>
                </div>
                <div className="flex gap-4 shrink-0">
                    <div className="flex flex-col items-end">
                        <span className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Risk Level</span>
                        <RiskIndicator level={summary.risk_score} />
                    </div>
                    <div className="flex flex-col items-end">
                        <span className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Screening Outcome</span>
                        <span className={`px-3 py-1 rounded-full text-xs font-bold border flex items-center gap-1 ${recommendationStyle(summary.recommendation)}`}>
                            <RecommendationIcon recommendation={summary.recommendation} />
                            {summary.recommendation}
                        </span>
                    </div>
                </div>
            </div>

            {summary.screening_coverage && summary.screening_coverage.is_complete === false && (
                <div className="bg-amber-950/40 border border-amber-800 rounded-xl p-4 flex gap-3 items-start">
                    <AlertTriangle size={18} className="text-amber-400 shrink-0 mt-0.5" />
                    <div>
                        <h3 className="text-xs font-bold text-amber-300 uppercase tracking-wide mb-1">Incomplete Screening Coverage</h3>
                        <p className="text-sm text-amber-100/80">
                            {summary.screening_coverage.description ||
                                `Only ${summary.screening_coverage.queries_succeeded ?? 0} of ${summary.screening_coverage.queries_attempted ?? 0} source queries completed.`}
                            {' '}This assessment is not a complete picture and must not be relied on without manual review.
                        </p>
                    </div>
                </div>
            )}

            {/* Compact / Paginated Human Review Queue */}
            {reviewItems.length > 0 && (
                <div className="overflow-hidden bg-slate-900 rounded-lg border border-amber-900/60 shadow-sm">
                    <div className="bg-amber-950/40 px-4 py-2.5 border-b border-amber-900/60 flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                            <UserCheck size={14} className="text-amber-400" />
                            <h3 className="font-bold text-xs text-amber-300 uppercase tracking-wide">
                                Requires Human Review ({reviewItems.length} Items)
                            </h3>
                        </div>
                        <div className="flex items-center gap-2">
                            {reviewItems.length > 4 && (
                                <button
                                    type="button"
                                    onClick={() => setExpandReviewQueue(prev => !prev)}
                                    className="text-[11px] font-semibold text-slate-300 hover:text-white px-2 py-0.5 rounded bg-slate-900/70 border border-slate-700"
                                >
                                    {expandReviewQueue ? 'Show Top 4' : `Show All ${reviewItems.length}`}
                                </button>
                            )}
                            {onOpenGovernance && (
                                <button
                                    type="button"
                                    onClick={onOpenGovernance}
                                    className="text-[11px] font-bold text-amber-200 hover:text-white px-2.5 py-0.5 rounded bg-amber-900/60 border border-amber-700"
                                >
                                    Disposition in Maker-Checker →
                                </button>
                            )}
                        </div>
                    </div>
                    <ul className="divide-y divide-slate-800">
                        {visibleReviewItems.map((item, i) => (
                            <li key={i} className="px-4 py-2.5">
                                <p className="text-xs font-semibold text-slate-200">{item.title ? `${item.title} — ${item.reason}` : item.reason}</p>
                                <p className="text-xs text-slate-400 mt-0.5">{item.detail}</p>
                                {item.citations && item.citations.length > 0 && (
                                    <div className="mt-1 flex gap-1 flex-wrap">
                                        {item.citations.slice(0, 3).map((url, ci) => (
                                            <a key={ci} href={url} target="_blank" rel="noopener noreferrer"
                                                className="inline-flex items-center text-[10px] text-blue-400 hover:text-blue-300 bg-blue-900/20 px-1.5 py-0.5 rounded border border-blue-800">
                                                Source {ci + 1} <ExternalLink size={8} className="ml-0.5" />
                                            </a>
                                        ))}
                                    </div>
                                )}
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            <div className="bg-slate-950/50 p-5 rounded-xl border border-slate-800 text-slate-300 leading-relaxed text-sm shadow-inner">
                <h3 className="text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-2 flex items-center gap-2"><User size={12} /> Profile Overview</h3>
                <div className="prose prose-sm prose-invert max-w-none prose-p:text-slate-300 prose-headings:text-slate-100">
                    <ReactMarkdown>{summary.summary}</ReactMarkdown>
                </div>
            </div>

            {summary.key_findings && summary.key_findings.length > 0 && (
                <div className="overflow-hidden bg-slate-900 rounded-lg border border-slate-800 shadow-sm">
                    <div className="bg-slate-950/80 px-4 py-2 border-b border-slate-800 backdrop-blur-sm">
                        <h3 className="font-bold text-xs text-slate-400 uppercase tracking-wide">Key Risk Factors</h3>
                    </div>
                    <table className="min-w-full divide-y divide-slate-800">
                        <thead className="bg-slate-900">
                            <tr>
                                <th className="px-4 py-2 text-left text-[10px] font-bold text-slate-500 uppercase w-2/3">Finding</th>
                                <th className="px-4 py-2 text-left text-[10px] font-bold text-slate-500 uppercase whitespace-nowrap">Date</th>
                                <th className="px-4 py-2 text-left text-[10px] font-bold text-slate-500 uppercase">Severity</th>
                            </tr>
                        </thead>
                        <tbody className="bg-slate-900 divide-y divide-slate-800">
                            {summary.key_findings.map((f, i) => (
                                <tr key={i} className="hover:bg-slate-800/50 transition-colors">
                                    <td className="px-4 py-3 text-sm text-slate-200">
                                        <span className="font-medium">{f.finding}</span>
                                        {f.citations && f.citations.length > 0 && (
                                            <div className="mt-1 flex gap-1 flex-wrap">
                                                {f.citations.slice(0, 3).map((url, ci) => (
                                                    <a key={ci} href={url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center text-[10px] text-blue-400 hover:text-blue-300 bg-blue-900/20 px-1.5 py-0.5 rounded border border-blue-800">
                                                        Source {ci + 1} <ExternalLink size={8} className="ml-0.5" />
                                                    </a>
                                                ))}
                                            </div>
                                        )}
                                    </td>
                                    <td className="px-4 py-3 text-xs text-slate-400 whitespace-nowrap font-mono">{f.date}</td>
                                    <td className="px-4 py-3">
                                        <span className={`inline-flex items-center px-2 py-0.5 text-[10px] font-bold rounded-full border ${f.severity === 'High' ? 'bg-red-900/30 text-red-300 border-red-800' :
                                            f.severity === 'Medium' ? 'bg-amber-900/30 text-amber-300 border-amber-800' :
                                                'bg-slate-800 text-slate-400 border-slate-700'}`}>
                                            {f.severity}
                                        </span>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </Card>
    );
};




function App() {
    const [status, setStatus] = useState('idle');
    const [progress, setProgress] = useState([]);
    const [results, setResults] = useState([]);
    const [summary, setSummary] = useState(null);
    const [isSearching, setIsSearching] = useState(false);
    const [costStats, setCostStats] = useState({ input_tokens: 0, output_tokens: 0, cost: 0 });
    // Screening-coverage alert. Held in its own state (not just the process log)
    // because a degraded or abandoned search must stay visible next to the
    // verdict, not scroll away in a list of progress messages.
    const [coverageAlert, setCoverageAlert] = useState(null);
    const [searchMode, setSearchMode] = useState('new');
    const [savedSubjects, setSavedSubjects] = useState([]);
    const [loadingSubjects, setLoadingSubjects] = useState(false);

    // New States for Sleek UI
    const [candidates, setCandidates] = useState([]);
    const [selectedCandidate, setSelectedCandidate] = useState(null);
    const [pendingSearch, setPendingSearch] = useState(null); // To store search params while disambiguating
    const [searchSubject, setSearchSubject] = useState(''); // For display

    const [priorityQueries, setPriorityQueries] = useState([]);
    const [generatedQueries, setGeneratedQueries] = useState([]);
    const [appConfig, setAppConfig] = useState(null);
    const [settings, setSettings] = useState({
        appName: 'KYC Agent',
        userName: 'Analyst',
        userProfession: 'Compliance Officer',
        logoUrl: 'https://www.gstatic.com/lamda/images/gemini_sparkle_v002_d4735304ff6292a690345.svg'
    });
    const [isSettingsModalOpen, setIsSettingsModalOpen] = useState(false);
    const [riskCategoryFilter, setRiskCategoryFilter] = useState('All');
    const [searchId, setSearchId] = useState(null);
    const [isConfirmModalOpen, setIsConfirmModalOpen] = useState(false);
    const [existingSubject, setExistingSubject] = useState(null);
    const [loadedGraphData, setLoadedGraphData] = useState(null);
    const [currentSubjectId, setCurrentSubjectId] = useState(null);
    const [watchlistData, setWatchlistData] = useState(null);
    const [watchlistStatus, setWatchlistStatus] = useState(null);
    const [refreshingLists, setRefreshingLists] = useState(false);
    const [governanceState, setGovernanceState] = useState(null);
    const [personaKey, setPersonaKey] = useState('maker');
    const [savingWatchlistHitId, setSavingWatchlistHitId] = useState(null);
    const [activeTab, setActiveTab] = useState('overview');
    const [lastHeartbeatAt, setLastHeartbeatAt] = useState(null);

    const eventSourceRef = useRef(null);

    const iapBaseEmail =
        governanceState?.reviewer?.authenticated_email ||
        appConfig?.REVIEWER?.authenticated_email ||
        'admin@ramneekkhurana.altostrat.com';

    const activePersona = useMemo(() => {
        if (personaKey === 'checker') {
            return {
                key: 'checker',
                role: 'CHECKER',
                email: 'compliance.checker@ramneekkhurana.altostrat.com',
            };
        }
        return {
            key: 'maker',
            role: 'MAKER',
            email: iapBaseEmail,
        };
    }, [personaKey, iapBaseEmail]);

    const fetchSubjectGovernance = async (subjId) => {
        if (!subjId) return;
        try {
            const res = await fetch(`/api/subjects/${subjId}/governance`, {
                headers: {
                    'X-KYC-Acting-Reviewer': activePersona.email,
                    'X-KYC-Acting-Role': activePersona.role,
                },
            });
            if (res.ok) {
                const gData = await res.json();
                setGovernanceState(gData);
                if (gData.watchlist_hits && gData.watchlist_hits.length > 0) {
                    setWatchlistData(prev => prev || {
                        status: gData.watchlist_hits.some(h => h.match_strength === 'CONFIRMED' || h.match_strength === 'STRONG')
                            ? 'SANCTIONS_HIT_DETECTED'
                            : 'POTENTIAL_MATCH_REVIEW_REQUIRED',
                        total_hits: gData.watchlist_hits.length,
                        confirmed_hits: gData.watchlist_hits.filter(h => h.match_strength === 'CONFIRMED').length,
                        strong_hits: gData.watchlist_hits.filter(h => h.match_strength === 'STRONG').length,
                        potential_hits: gData.watchlist_hits.filter(h => h.match_strength === 'POTENTIAL').length,
                        hits: gData.watchlist_hits,
                        coverage: watchlistStatus,
                    });
                }
            }
        } catch (err) {
            console.error('Failed to load subject governance:', err);
        }
    };

    const handleRefreshWatchlists = async () => {
        setRefreshingLists(true);
        try {
            const res = await fetch('/api/watchlist/refresh', { method: 'POST' });
            const cov = await res.json();
            setWatchlistStatus(cov);
            if (watchlistData) {
                setWatchlistData(prev => ({ ...prev, coverage: cov }));
            }
        } catch (err) {
            console.error('Watchlist refresh error:', err);
        } finally {
            setRefreshingLists(false);
        }
    };

    const handleSaveWatchlistHitDisposition = async ({ item_id, item_type, item_title, verdict, rationale }) => {
        if (!currentSubjectId) return;
        setSavingWatchlistHitId(item_id);
        try {
            const res = await fetch(`/api/subjects/${currentSubjectId}/dispositions`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-KYC-Acting-Reviewer': activePersona.email,
                    'X-KYC-Acting-Role': activePersona.role,
                },
                body: JSON.stringify({ item_id, item_type, item_title, verdict, rationale }),
            });
            const data = await res.json();
            if (res.ok) {
                setGovernanceState(prev => ({
                    ...(prev || {}),
                    case_review: data.case_review,
                    dispositions: data.dispositions,
                    audit_events: data.audit_events,
                    audit_chain_verification: data.audit_chain_verification,
                }));
            } else {
                alert(data.error || 'Failed to record watchlist disposition');
            }
        } catch (err) {
            console.error(err);
        } finally {
            setSavingWatchlistHitId(null);
        }
    };

    // Initial Data Fetch
    useEffect(() => {
        fetch('/api/config').then(res => res.json()).then(setAppConfig).catch(console.error);
        fetch('/api/subjects').then(res => res.json()).then(setSavedSubjects).catch(console.error);
        fetch('/api/watchlist/status').then(res => res.json()).then(setWatchlistStatus).catch(console.error);
    }, []);

    const handleLoadSubject = async (subjectId) => {
        setLoadingSubjects(true);
        setStatus('loading_record');
        setResults([]);
        setSummary(null);
        setCoverageAlert(null);
        setWatchlistData(null);
        setGovernanceState(null);
        setCurrentSubjectId(subjectId);
        setProgress([]);
        setCandidates([]);
        setSelectedCandidate(null);
        setSearchId(Date.now()); // Force refresh

        try {
            const res = await fetch(`/api/subjects/${subjectId}`);
            if (!res.ok) throw new Error("Failed to load subject");
            const data = await res.json();

            let parsedSummary = null;
            try {
                parsedSummary = data.subject.summary ? (typeof data.subject.summary === 'string' ? JSON.parse(data.subject.summary) : data.subject.summary) : null;
                setSummary(parsedSummary);
            } catch (e) {
                console.warn("Failed to parse summary JSON", e);
                setSummary(data.subject.summary);
            }
            if (parsedSummary && parsedSummary.watchlist_screening) {
                setWatchlistData(parsedSummary.watchlist_screening);
            } else if (data.watchlist_hits) {
                const hits = data.watchlist_hits;
                setWatchlistData({
                    status: hits.some(h => h.match_strength === 'CONFIRMED' || h.match_strength === 'STRONG')
                        ? 'SANCTIONS_HIT_DETECTED'
                        : hits.length > 0
                            ? 'POTENTIAL_MATCH_REVIEW_REQUIRED'
                            : 'CLEAR',
                    total_hits: hits.length,
                    confirmed_hits: hits.filter(h => h.match_strength === 'CONFIRMED').length,
                    strong_hits: hits.filter(h => h.match_strength === 'STRONG').length,
                    potential_hits: hits.filter(h => h.match_strength === 'POTENTIAL').length,
                    hits,
                    coverage: watchlistStatus,
                });
            }
            setGovernanceState({
                subject_id: subjectId,
                reviewer: data.reviewer,
                case_review: data.case_review,
                dispositions: data.dispositions || {},
                watchlist_hits: data.watchlist_hits || [],
                audit_events: data.audit_events || [],
                audit_chain_verification: data.audit_chain_verification || { valid: true, event_count: 0 },
            });
            setResults(data.findings || []);
            setExistingSubject(data.subject);
            setSearchSubject(data.subject.name);
            setLoadedGraphData(data.graph_data); // Load saved graph

            // Populate Identity Panel with loaded data
            setSelectedCandidate({
                name: data.subject.name,
                role: 'Loaded Record',
                org: 'Database',
                location: 'N/A'
            });

        } catch (err) {
            console.error(err);
            alert("Error loading record");
        } finally {
            setLoadingSubjects(false);
            setStatus('idle');
        }
    };

    const executeSearch = (formData, customKeywords, options = {}) => {
        if (eventSourceRef.current) {
            eventSourceRef.current.close();
        }

        setStatus('searching');
        setProgress([]);
        setResults([]);
        setSummary(null);
        setCoverageAlert(null);
        setWatchlistData(null);
        setGovernanceState(null);
        setCandidates([]);
        setPriorityQueries([]);
        setGeneratedQueries([]);
        setCostStats({ input_tokens: 0, output_tokens: 0, cost: 0 });
        setSearchId(Date.now());
        setSearchSubject(formData.subjectName);
        setLoadedGraphData(null);

        const queryParams = new URLSearchParams({
            subjectName: formData.subjectName,
            alias: formData.alias || '',
            profession: formData.profession || '',
            company: formData.company || '',
            region: formData.region || '',
            dob: formData.dob || '',
            age: formData.age || '',
            ownership: formData.ownership || '',
            spouse: formData.spouse || '',
            customKeywords: customKeywords.join(','),
            recency_days: '3650',
            ...options
        });

        const es = new EventSource(`/api/kyc-check?${queryParams.toString()}`);
        eventSourceRef.current = es;
        let streamedSubjectId = null;
        let streamCompleted = false;

        es.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);

                switch (data.status) {
                    case 'heartbeat':
                        setLastHeartbeatAt(new Date().toLocaleTimeString());
                        if (data.subject_id && !streamedSubjectId) {
                            streamedSubjectId = data.subject_id;
                            setCurrentSubjectId(data.subject_id);
                        }
                        break;
                    case 'subject_initialized':
                        setLastHeartbeatAt(new Date().toLocaleTimeString());
                        if (data.subject_id) {
                            streamedSubjectId = data.subject_id;
                            setCurrentSubjectId(data.subject_id);
                        }
                        break;
                    case 'watchlist_results':
                        setWatchlistData(data.data);
                        if (data.subject_id) {
                            streamedSubjectId = data.subject_id;
                            setCurrentSubjectId(data.subject_id);
                            fetchSubjectGovernance(data.subject_id);
                        }
                        setProgress(prev => [...prev, data]);
                        break;
                    case 'queries_generated':
                        setGeneratedQueries(data.data || []);
                        setProgress(prev => [...prev, data]);
                        break;
                    case 'usage_update':
                        setCostStats(data.data);
                        break;
                    case 'kyc_result_generated':
                        setResults(prev => {
                            const newResults = [...prev, data.data];
                            // Sort by priority then relevance
                            newResults.sort((a, b) => {
                                const aIsPriority = a.source_type === 'priority_search';
                                const bIsPriority = b.source_type === 'priority_search';
                                if (aIsPriority && !bIsPriority) return -1;
                                if (!aIsPriority && bIsPriority) return 1;
                                return b.relevance_score - a.relevance_score;
                            });
                            return newResults;
                        });
                        break;
                    case 'kyc_summary_generated':
                        console.log("Summary received:", data.data);
                        setSummary(data.data);
                        if (data.data?.watchlist_screening) {
                            setWatchlistData(data.data.watchlist_screening);
                        }
                        if (data.subject_id) {
                            streamedSubjectId = data.subject_id;
                            fetchSubjectGovernance(data.subject_id);
                        }
                        setProgress(prev => [...prev, data]);
                        break;
                    case 'kyc_graph_generated':
                        setLoadedGraphData(data.data);
                        setProgress(prev => [...prev, { status: "graph_ready", message: "Entity Graph received." }]);
                        break;
                    case 'graph_data':
                        setLoadedGraphData(data.data);
                        break;
                    case 'degraded_coverage':
                        setCoverageAlert({ severity: 'warning', message: data.message });
                        setProgress(prev => [...prev, data]);
                        break;
                    case 'screening_incomplete':
                        setCoverageAlert({ severity: 'critical', message: data.message });
                        setProgress(prev => [...prev, data]);
                        break;
                    case 'complete':
                        streamCompleted = true;
                        if (data.screening_incomplete) {
                            setCoverageAlert({ severity: 'critical', message: data.message });
                        }
                        if (data.subject_id) {
                            fetchSubjectGovernance(data.subject_id);
                        }
                        fetch('/api/subjects').then(res => res.json()).then(setSavedSubjects).catch(console.error);
                        setIsSearching(false);
                        es.close();
                        eventSourceRef.current = null;
                        setProgress(prev => [...prev, data]);
                        break;
                    case 'error':
                        streamCompleted = true;
                        setIsSearching(false);
                        es.close();
                        eventSourceRef.current = null;
                        setProgress(prev => [...prev, data]);
                        break;
                    default:
                        setProgress(prev => [...prev, data]);
                        break;
                }
            } catch (error) {
                console.error("Event parsing failed:", error);
            }
        };

        es.onerror = (err) => {
            console.warn("EventSource connection interrupted:", err);
            es.close();
            eventSourceRef.current = null;

            // If the SSE stream dropped mid-run (e.g. proxy idle reset or tab
            // sleep), the server worker continues persisting findings, summary,
            // and graph to Spanner. Poll Spanner until completion and hydrate.
            if (!streamCompleted && streamedSubjectId) {
                setProgress(prev => [
                    ...prev,
                    {
                        status: 'reconnecting',
                        message: 'Stream interrupted — syncing completed assessment from Spanner...',
                    },
                ]);
                let attempts = 0;
                const pollInterval = setInterval(async () => {
                    attempts += 1;
                    try {
                        const res = await fetch(`/api/subjects/${streamedSubjectId}`);
                        if (res.ok) {
                            const sData = await res.json();
                            if (sData?.subject?.summary) {
                                clearInterval(pollInterval);
                                setIsSearching(false);
                                fetch('/api/subjects').then(r => r.json()).then(setSavedSubjects).catch(console.error);
                                handleLoadSubject(streamedSubjectId);
                                return;
                            }
                        }
                    } catch (pollErr) {
                        console.warn("Spanner recovery poll error:", pollErr);
                    }
                    if (attempts >= 36) {
                        clearInterval(pollInterval);
                        setIsSearching(false);
                    }
                }, 4000);
            } else {
                setIsSearching(false);
            }
        };
    };

    const submitLock = useRef(false);

    const performSearch = async (formData, customKeywords) => {
        if (submitLock.current) return;
        submitLock.current = true;
        setIsSearching(true);
        setPendingSearch({ formData, customKeywords }); // Store for potential resume

        try {
            // New UI: Mock Disambiguation (Optional Feature)
            // Just checks if name is common (Mock check)
            /*
            if (formData.subjectName.toLowerCase().includes('smith')) {
                setCandidates([
                    { name: formData.subjectName, role: 'CEO', org: 'Tech Corp', dob: '1980' },
                    { name: formData.subjectName, role: 'Driver', org: 'Logistics Inc', dob: '1992' }
                ]);
                setIsSearching(false);
                submitLock.current = false;
                return;
            }
            */

            // Check existing subject (Real Backend Check)
            if (searchMode === 'new') {
                const res = await fetch(`/api/subjects/check?name=${encodeURIComponent(formData.subjectName)}`);
                const data = await res.json();
                if (data.exists && data.subject) {
                    setExistingSubject(data.subject);
                    setIsConfirmModalOpen(true); // Open Modal
                    setIsSearching(false);
                    submitLock.current = false;
                    return;
                }
            }

            // Normal Flow
            setSelectedCandidate({ // Set "Target Profile" immediately
                name: formData.subjectName,
                role: formData.profession,
                org: formData.company,
                location: formData.region
            });
            executeSearch(formData, customKeywords);

        } catch (e) {
            console.error(e);
            executeSearch(formData, customKeywords); // Fallback
        } finally {
            submitLock.current = false;
        }
    };

    // Modal Handlers
    const handleConfirmUpdate = () => {
        setIsConfirmModalOpen(false);
        if (pendingSearch && existingSubject) {
            executeSearch(pendingSearch.formData, pendingSearch.customKeywords, {
                incremental: true,
                subject_id: existingSubject.subject_id
            });
        }
    };
    const handleConfirmNew = () => {
        setIsConfirmModalOpen(false);
        if (pendingSearch) {
            executeSearch(pendingSearch.formData, pendingSearch.customKeywords, { incremental: false });
        }
    };
    const handleConfirmView = () => {
        setIsConfirmModalOpen(false);
        if (existingSubject) handleLoadSubject(existingSubject.subject_id);
    };

    const handleCandidateSelect = (candidate) => {
        setSelectedCandidate(candidate);
        setCandidates([]); // Clear selection list
        // Proceed with search using candidate details...
        // For now, just trigger what was pending
        if (pendingSearch) {
            executeSearch(pendingSearch.formData, pendingSearch.customKeywords);
        }
    };

    // Header Component
    const Header = ({ settings, onOpenSettings, costStats }) => (
        <header className="bg-slate-900 border-b border-slate-800 sticky top-0 z-40">
            <div className="px-6 mx-auto">
                <div className="flex items-center justify-between h-16">
                    <div className="flex items-center gap-4">
                        <img src={settings.logoUrl} alt="Logo" className="h-8" onError={(e) => { e.target.onerror = null; e.target.src = 'https://blog.e2info.co.jp/wp-content/uploads/2021/12/gcp.png'; }} />
                        <div>
                            <h1 className="text-lg font-bold text-white leading-tight">{settings.appName}</h1>
                            <p className="text-xs text-slate-400">KYC Adverse Media Screening</p>
                        </div>
                    </div>
                    <div className="flex items-center gap-4">
                        <CostDisplay stats={costStats} />
                        <div className="w-8 h-8 rounded-full bg-slate-700 flex items-center justify-center font-bold text-slate-300 border border-slate-600">
                            {settings.userName.substring(0, 2).toUpperCase()}
                        </div>
                    </div>
                </div>
            </div>
        </header>
    );

    // --- Confirmation Modal ---
    const ConfirmationModal = ({ isOpen, onClose, subject, onConfirmUpdate, onConfirmNew, onConfirmView }) => {
        if (!isOpen) return null;
        return (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm animate-fade-in">
                <div className="bg-white rounded-xl shadow-2xl p-6 max-w-md w-full m-4">
                    <div className="flex items-center gap-3 text-amber-600 mb-4">
                        <AlertTriangle size={24} />
                        <h3 className="text-lg font-bold text-gray-900">Subject Already Exists</h3>
                    </div>
                    <p className="text-gray-600 mb-6">
                        An investigation record for <strong>{subject?.name}</strong> already exists (Last updated: {subject?.last_updated ? new Date(subject.last_updated).toLocaleDateString() : 'Unknown'}).
                    </p>
                    <div className="flex flex-col gap-3">
                        <Button onClick={onConfirmUpdate} className="w-full justify-center !bg-blue-600">
                            Update Record (Incremental Scan)
                        </Button>
                        <Button onClick={onConfirmView} variant="secondary" className="w-full justify-center">
                            View Existing Record
                        </Button>
                        <div className="relative my-2">
                            <div className="absolute inset-0 flex items-center"><span className="w-full border-t border-gray-300"></span></div>
                            <div className="relative flex justify-center text-xs uppercase"><span className="bg-white px-2 text-gray-500">Or</span></div>
                        </div>
                        <Button onClick={onConfirmNew} className="w-full justify-center !bg-red-600 hover:!bg-red-700">
                            Start Fresh Investigation (Overwrite)
                        </Button>
                        <button onClick={onClose} className="mt-2 text-gray-400 text-sm hover:text-gray-600 self-center">Cancel</button>
                    </div>
                </div>
            </div>
        );
    };

    // Helper to extract Key Profile Data from Graph
    const extractProfileFromGraph = (graph, subjectName) => {
        if (!graph || !graph.nodes || !graph.edges) return {};

        // 1. Find Subject Node with robust matching
        const normalizedInput = subjectName?.toLowerCase().trim();
        if (!normalizedInput) return {};

        const nameParts = normalizedInput.split(/\s+/).filter(p => p.length > 2);

        // Score nodes based on match quality
        const scoredNodes = graph.nodes.map(n => {
            const label = n.label.toLowerCase();
            let score = 0;
            if (label === normalizedInput) score += 10;
            else if (label.includes(normalizedInput)) score += 5;
            else {
                // Count overlapping parts
                const partsMatched = nameParts.filter(p => label.includes(p)).length;
                if (partsMatched > 0) score += partsMatched;
                // Bonus for group='person'
                if (n.group === 'person') score += 1;
            }
            return { node: n, score };
        });

        // Pick best match (threshold > 0)
        scoredNodes.sort((a, b) => b.score - a.score);
        const bestMatch = scoredNodes[0];

        if (!bestMatch || bestMatch.score < 1) return {};

        const subjectNode = bestMatch.node;

        const subjectId = subjectNode.id;
        const connectedEdges = graph.edges.filter(e => e.from === subjectId || e.to === subjectId);

        // 2. Traverse Edges
        const associates = new Set();
        const ownership = new Set();
        const locations = new Set();
        let age = null;

        graph.edges.forEach(e => {
            if (e.from !== subjectId && e.to !== subjectId) return;

            const otherId = e.from === subjectId ? e.to : e.from;
            const otherNode = graph.nodes.find(n => n.id === otherId);
            if (!otherNode) return;

            const group = otherNode.group?.toLowerCase();
            const label = e.label?.toLowerCase() || '';

            // Locations
            if (group === 'location') {
                locations.add(otherNode.label);
            }

            // Associates (Persons)
            if (group === 'person') {
                associates.add(otherNode.label);
            }

            // Ownership / Role (Organizations)
            if (group === 'organization') {
                ownership.add(otherNode.label);
            }

            // Age extraction from edge label (e.g., "aged 53")
            if (label.includes('age') || label.includes('born')) {
                const match = label.match(/\d{4}/) || label.match(/\d{2}/);
                if (match) age = match[0]; // simplistic year/age year extraction
            }
        });

        // 3. Normalize & Format Output
        const formatSet = (set, limit) => Array.from(set).slice(0, limit).join(', ') || null;

        return {
            associates: formatSet(associates, 5),
            ownership: formatSet(ownership, 5),
            location: formatSet(locations, 3),
            age: age
        };
    };

    const graphDerivedProfile = useMemo(() =>
        extractProfileFromGraph(loadedGraphData, searchSubject),
        [loadedGraphData, searchSubject]);

    const hasActiveCase = Boolean(
        isSearching || summary || results.length > 0 || watchlistData || currentSubjectId || searchSubject
    );
    const reviewQueueItems = summary?.requires_human_review || [];
    const dispMap = governanceState?.dispositions || {};
    const undispositionedCount = reviewQueueItems.filter(
        (it, idx) => !dispMap[it.finding_id || `review-item-${idx}`]
    ).length;

    return (
        <div className="bg-slate-950 font-sans text-slate-50 min-h-screen flex flex-col">
            <Header settings={settings} onOpenSettings={() => setIsSettingsModalOpen(true)} costStats={costStats} />

            {/* Sticky Production Telemetry & Pipeline Status Bar */}
            <LiveSystemStatusBar
                isSearching={isSearching}
                status={status}
                progress={progress}
                resultsCount={results.length}
                summary={summary}
                watchlistData={watchlistData}
                watchlistStatus={watchlistStatus}
                governanceState={governanceState}
                lastHeartbeatAt={lastHeartbeatAt}
                searchSubject={searchSubject}
            />

            <main className="flex-grow px-6 py-5 max-w-[1600px] w-full mx-auto flex flex-col gap-5 pb-24">
                <SearchArea
                    onSearch={(fd, kw) => {
                        setActiveTab('overview');
                        performSearch(fd, kw);
                    }}
                    isSearching={isSearching}
                    searchMode={searchMode}
                    setSearchMode={setSearchMode}
                    savedSubjects={savedSubjects}
                    onLoadSubject={(sid) => {
                        setActiveTab('overview');
                        handleLoadSubject(sid);
                    }}
                    loadingSubjects={loadingSubjects}
                    initialFormData={pendingSearch?.formData}
                />

                {coverageAlert && (
                    <div className={`rounded-xl p-4 flex gap-3 items-start border ${coverageAlert.severity === 'critical'
                        ? 'bg-red-950/40 border-red-800'
                        : 'bg-amber-950/40 border-amber-800'}`}>
                        <AlertTriangle
                            size={18}
                            className={`shrink-0 mt-0.5 ${coverageAlert.severity === 'critical' ? 'text-red-400' : 'text-amber-400'}`}
                        />
                        <div>
                            <h3 className={`text-xs font-bold uppercase tracking-wide mb-1 ${coverageAlert.severity === 'critical' ? 'text-red-300' : 'text-amber-300'}`}>
                                {coverageAlert.severity === 'critical'
                                    ? 'Screening Incomplete'
                                    : 'Degraded Search Coverage'}
                            </h3>
                            <p className={`text-sm ${coverageAlert.severity === 'critical' ? 'text-red-100/80' : 'text-amber-100/80'}`}>
                                {coverageAlert.message}
                                {coverageAlert.severity === 'critical' &&
                                    ' No conclusion about this subject can be drawn from this run.'}
                            </p>
                        </div>
                    </div>
                )}

                {hasActiveCase ? (
                    <div>
                        <CaseWorkspaceTabs
                            activeTab={activeTab}
                            onChangeTab={setActiveTab}
                            watchlistHitsCount={watchlistData?.total_hits || 0}
                            watchlistStatusLabel={watchlistData?.status || 'CLEAR'}
                            reviewQueueCount={reviewQueueItems.length}
                            undispositionedCount={undispositionedCount}
                            reviewState={governanceState?.case_review?.review_state || 'UNREVIEWED'}
                            resultsCount={results.length}
                        />

                        {/* TAB 1: EXECUTIVE OVERVIEW */}
                        {activeTab === 'overview' && (
                            <div className="space-y-5 animate-fade-in">
                                {(watchlistData || watchlistStatus) && (
                                    <ErrorBoundary>
                                        <WatchlistScreeningPanel
                                            compact={true}
                                            watchlistData={watchlistData}
                                            watchlistStatus={watchlistStatus}
                                            onOpenFullWatchlist={() => setActiveTab('watchlist')}
                                        />
                                    </ErrorBoundary>
                                )}

                                {summary ? (
                                    <ErrorBoundary>
                                        <SummaryReport
                                            summary={summary}
                                            onOpenGovernance={() => setActiveTab('governance')}
                                        />
                                    </ErrorBoundary>
                                ) : isSearching ? (
                                    <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 flex items-center justify-between gap-4">
                                        <div className="flex items-center gap-4">
                                            <Spinner size="sm" />
                                            <div>
                                                <h3 className="text-sm font-bold text-slate-100">
                                                    Synthesizing Screening Evidence for {searchSubject}...
                                                </h3>
                                                <p className="text-xs text-slate-400 mt-0.5">
                                                    {progress[progress.length - 1]?.message || 'Running multi-angle adverse media discovery & full-text risk scoring...'}
                                                    {' '}({results.length} sources analyzed so far)
                                                </p>
                                            </div>
                                        </div>
                                        <button
                                            type="button"
                                            onClick={() => setActiveTab('sources')}
                                            className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-blue-300 border border-slate-700 shrink-0"
                                        >
                                            View Live Feed ({results.length}) →
                                        </button>
                                    </div>
                                ) : null}

                                {/* Show live incoming sources table while searching or if summary not yet generated */}
                                {(isSearching || !summary) && results.length > 0 && (
                                    <ResultsTable
                                        results={results}
                                        riskCategoryFilter={riskCategoryFilter}
                                        onFilterChange={setRiskCategoryFilter}
                                        appConfig={appConfig}
                                        searchId={searchId}
                                        subjectName={searchSubject || 'Subject'}
                                        setCostStats={setCostStats}
                                        loadedGraphData={loadedGraphData}
                                    />
                                )}
                            </div>
                        )}

                        {/* TAB 2: SANCTIONS & WATCHLISTS */}
                        {activeTab === 'watchlist' && (
                            <div className="animate-fade-in">
                                <ErrorBoundary>
                                    <WatchlistScreeningPanel
                                        watchlistData={watchlistData}
                                        watchlistStatus={watchlistStatus}
                                        onRefreshLists={handleRefreshWatchlists}
                                        refreshingLists={refreshingLists}
                                        dispositions={governanceState?.dispositions || {}}
                                        onSaveDisposition={currentSubjectId ? handleSaveWatchlistHitDisposition : null}
                                        savingItemId={savingWatchlistHitId}
                                        isCaseLocked={governanceState?.case_review?.review_state === 'APPROVED'}
                                    />
                                </ErrorBoundary>
                            </div>
                        )}

                        {/* TAB 3: FOUR-EYES MAKER-CHECKER GOVERNANCE & AUDIT TRAIL */}
                        {activeTab === 'governance' && (
                            <div className="animate-fade-in">
                                {currentSubjectId ? (
                                    <ErrorBoundary>
                                        <MakerCheckerGovernancePanel
                                            subjectId={currentSubjectId}
                                            subjectName={searchSubject}
                                            summary={summary}
                                            watchlistHits={watchlistData?.hits || governanceState?.watchlist_hits || []}
                                            governanceState={governanceState}
                                            activePersona={activePersona}
                                            onChangePersona={setPersonaKey}
                                            onRefreshGovernance={() => fetchSubjectGovernance(currentSubjectId)}
                                        />
                                    </ErrorBoundary>
                                ) : (
                                    <div className="p-8 text-center text-sm text-slate-400 bg-slate-900 rounded-xl border border-slate-800">
                                        Run a search or load a subject record to access Four-Eyes Maker-Checker Governance.
                                    </div>
                                )}
                            </div>
                        )}

                        {/* TAB 4: ADVERSE MEDIA SOURCES & ENTITY GRAPH */}
                        {activeTab === 'sources' && (
                            <div className="animate-fade-in">
                                <ResultsTable
                                    results={results}
                                    riskCategoryFilter={riskCategoryFilter}
                                    onFilterChange={setRiskCategoryFilter}
                                    appConfig={appConfig}
                                    searchId={searchId}
                                    subjectName={searchSubject || 'Subject'}
                                    setCostStats={setCostStats}
                                    loadedGraphData={loadedGraphData}
                                />
                            </div>
                        )}

                        {/* TAB 5: IDENTITY PROFILE & DOCUMENTS */}
                        {activeTab === 'profile' && (
                            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 animate-fade-in">
                                <div className="lg:col-span-6">
                                    <IdentityPanel
                                        selectedCandidate={selectedCandidate}
                                        candidates={candidates}
                                        onSelectCandidate={handleCandidateSelect}
                                        subjectProfile={{
                                            ...(pendingSearch?.formData || {}),
                                            ...(existingSubject?.profile_data || {}),
                                            ...graphDerivedProfile
                                        }}
                                    />
                                </div>
                                <div className="lg:col-span-6 h-[420px]">
                                    <DocumentChecker
                                        subjectId={existingSubject?.subject_id || searchId}
                                        subjectName={existingSubject?.name || searchSubject}
                                        onProfileUpdate={() => {
                                            if (existingSubject?.subject_id) handleLoadSubject(existingSubject.subject_id);
                                        }}
                                    />
                                </div>
                            </div>
                        )}
                    </div>
                ) : (
                    /* Clean Idle Landing State: Watchlist Readiness Strip */
                    <ErrorBoundary>
                        <WatchlistScreeningPanel
                            watchlistData={null}
                            watchlistStatus={watchlistStatus}
                            onRefreshLists={handleRefreshWatchlists}
                            refreshingLists={refreshingLists}
                        />
                    </ErrorBoundary>
                )}
            </main>

            {/* Collapsible Process Log (Fixed Bottom) */}
            <ProcessLog
                progress={progress}
                isSearching={isSearching}
                priorityQueries={priorityQueries}
                generatedQueries={generatedQueries}
            />

            <ConfirmationModal
                isOpen={isConfirmModalOpen}
                onClose={() => setIsConfirmModalOpen(false)}
                subject={existingSubject}
                onConfirmUpdate={handleConfirmUpdate}
                onConfirmNew={handleConfirmNew}
                onConfirmView={handleConfirmView}
            />

            {/* Styles for Summary Table specifically (override tailwind prose for tables) */}
            <style>{`
                .prose table { font-size: 0.875rem; }
                .prose thead th { text-align: left; }
                /* Ensure modal content is visible against dark theme */
                .text-gray-900 { color: #111827 !important; } 
                .text-gray-800 { color: #1f2937 !important; }
                .text-gray-600 { color: #4b5563 !important; }
                .bg-white { background-color: #ffffff !important; }
            `}</style>
        </div>
    );
}

export default App;
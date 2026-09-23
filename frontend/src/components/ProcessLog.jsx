import React, { useState, useEffect, useRef } from 'react';
import { Activity, ChevronUp, ChevronDown, CheckCircle, Search, Brain, Share2 } from 'lucide-react';

const ProcessLog = ({ progress, isSearching, priorityQueries, generatedQueries }) => {
    const [isExpanded, setIsExpanded] = useState(false);
    const scrollRef = useRef(null);

    // Auto-scroll when new logs arrive, only if expanded
    useEffect(() => {
        if (isExpanded && scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [progress, isExpanded]);

    if (!progress || progress.length === 0) return null;

    const lastLog = progress[progress.length - 1];
    const statusText = lastLog?.message || "Initializing...";
    const isError = lastLog?.status === 'error';
    const isComplete = lastLog?.status === 'complete';

    let statusColor = "text-blue-400";
    if (isError) statusColor = "text-red-400";
    if (isComplete) statusColor = "text-emerald-400";

    return (
        <div className="fixed bottom-0 left-0 right-0 z-50 flex flex-col items-center">
            {/* Expanded Pane */}
            {isExpanded && (
                <div className="w-full max-w-5xl bg-slate-900 border-t border-l border-r border-slate-700 rounded-t-xl shadow-2xl overflow-hidden animate-slide-up">
                    <div className="bg-slate-800 px-4 py-2 border-b border-slate-700 flex justify-between items-center">
                        <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">Detailed Process Log</span>
                        <button onClick={() => setIsExpanded(false)} className="text-slate-400 hover:text-white p-1">
                            <ChevronDown size={16} />
                        </button>
                    </div>
                    <div ref={scrollRef} className="h-64 overflow-y-auto p-4 space-y-3 font-mono text-xs">
                        {progress.map((p, i) => (
                            <LogItem key={i} log={p} priorityQueries={priorityQueries} generatedQueries={generatedQueries} />
                        ))}
                        {isSearching && (
                            <div className="flex items-center gap-2 text-slate-500 animate-pulse">
                                <span className="w-2 h-2 bg-blue-500 rounded-full"></span>
                                Processing...
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Status Bar (Always Visible) */}
            <div
                onClick={() => setIsExpanded(!isExpanded)}
                className="w-full bg-slate-900 border-t border-slate-800 py-2 px-6 flex items-center justify-between cursor-pointer hover:bg-slate-800 transition-colors shadow-lg"
            >
                <div className="flex items-center gap-4 max-w-4xl mx-auto w-full">
                    <Activity size={18} className={isSearching ? "text-blue-500 animate-pulse" : "text-emerald-500"} />
                    <div className="h-4 w-px bg-slate-700"></div>
                    <span className={`text-sm font-medium ${statusColor} truncate flex-grow`}>
                        {statusText}
                    </span>
                    <div className="flex items-center gap-2 text-slate-500 text-xs font-medium uppercase tracking-wider">
                        {isSearching ? 'Running' : 'Ready'}
                        {isExpanded ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
                    </div>
                </div>
            </div>
        </div>
    );
};

const LogItem = ({ log, priorityQueries, generatedQueries }) => {
    // Render icon based on status type
    const getIcon = () => {
        switch (log.status) {
            case 'complete': return <CheckCircle size={14} className="text-emerald-500" />;
            case 'error': return <span className="text-red-500">✖</span>;
            case 'performing_priority_searches': return <Search size={14} className="text-amber-500" />;
            case 'queries_generated': return <Brain size={14} className="text-purple-500" />;
            case 'performing_distributed_searches': return <Share2 size={14} className="text-blue-500" />;
            default: return <span className="text-slate-600">➜</span>;
        }
    };

    return (
        <div className="text-slate-300">
            <div className="flex items-start gap-3">
                <div className="mt-0.5">{getIcon()}</div>
                <div>{log.message}</div>
            </div>
            {/* Show Queries Detail if applicable */}
            {log.status === 'queries_generated' && generatedQueries?.length > 0 && (
                <div className="ml-6 mt-1 p-2 bg-slate-800 rounded border border-slate-700 text-slate-400">
                    <div className="mb-1 text-[10px] uppercase font-bold opacity-70">Sample Queries:</div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-1 line-clamp-4">
                        {generatedQueries.slice(0, 6).map((q, i) => <div key={i}>• {q}</div>)}
                    </div>
                    {generatedQueries.length > 6 && <div className="mt-1 italic opacity-50">...and {generatedQueries.length - 6} more</div>}
                </div>
            )}
        </div>
    );
};

export default ProcessLog;

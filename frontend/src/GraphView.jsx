import React, { useEffect, useRef, useState, useMemo } from 'react';
import { Network } from 'vis-network';
import { Play, Pause, SkipBack, SkipForward } from 'lucide-react';

const GraphView = ({ nodes, edges, subjectName }) => {
    const containerRef = useRef(null);
    const networkRef = useRef(null);
    const [timelineYear, setTimelineYear] = useState(null);
    const [isPlaying, setIsPlaying] = useState(false);
    const [yearRange, setYearRange] = useState({ min: 2000, max: new Date().getFullYear() });

    // --- 1. Parse Years and Determine Range ---
    const { processedNodes, processedEdges, validYears } = useMemo(() => {
        if (!nodes || !edges) return { processedNodes: [], processedEdges: [], validYears: [] };

        // Process Nodes (Highlight Subject)
        const procNodes = nodes.map(node => {
            if (subjectName && node.label && node.label.toLowerCase() === subjectName.toLowerCase()) {
                return {
                    ...node,
                    size: 40,
                    color: { background: '#ef4444', border: '#b91c1c' },
                    font: { size: 20, face: 'arial', color: '#b91c1c', strokeWidth: 2, strokeColor: '#ffffff' },
                    shadow: { enabled: true }
                };
            }
            return node;
        });

        // Process Edges & Extract Years
        const years = new Set();
        const procEdges = edges.map(edge => {
            let edgeYear = null;
            if (edge.year && edge.year !== 'Unknown') {
                // Try parsing "2023", "approx 2021", etc.
                const match = edge.year.toString().match(/\b(19|20)\d{2}\b/);
                if (match) {
                    edgeYear = parseInt(match[0], 10);
                    years.add(edgeYear);
                }
            }
            return { ...edge, parsedYear: edgeYear };
        });

        // If no years found, default to current range
        if (years.size === 0) {
            years.add(new Date().getFullYear());
        }

        return { processedNodes: procNodes, processedEdges: procEdges, validYears: Array.from(years).sort((a, b) => a - b) };
    }, [nodes, edges, subjectName]);

    // --- 2. Update Range State ---
    useEffect(() => {
        if (validYears.length > 0) {
            const min = validYears[0];
            const max = validYears[validYears.length - 1];
            setYearRange({ min, max });
            // Initialize timeline to max (show all) ONLY if it wasn't set or is out of bounds
            setTimelineYear(prev => (prev === null || prev < min || prev > max) ? max : prev);
        }
    }, [validYears]);

    // --- 3. Filter Data based on Timeline ---
    const visibleData = useMemo(() => {
        if (!timelineYear) return { nodes: processedNodes, edges: processedEdges };

        // Filter Edges: Show if year is unknown OR year <= timelineYear
        const filteredEdges = processedEdges.filter(e => {
            if (e.parsedYear === null) return true; // Show "Unknown" / timeless edges always
            return e.parsedYear <= timelineYear;
        });

        // Filter Nodes: Keep nodes that are connected by visible edges OR are significant (like the subject)
        // OR filtering nodes might detach them. Usually keeping all nodes is less jarring layout-wise,
        // but hiding disconnected ones is cleaner. Let's hide completely disconnected ones except Subject.

        const connectedNodeIds = new Set();
        filteredEdges.forEach(e => {
            connectedNodeIds.add(e.from);
            connectedNodeIds.add(e.to);
        });

        const filteredNodes = processedNodes.filter(n => {
            // Always show subject
            if (subjectName && n.label && n.label.toLowerCase() === subjectName.toLowerCase()) return true;
            return connectedNodeIds.has(n.id);
        });

        return { nodes: filteredNodes, edges: filteredEdges };
    }, [processedNodes, processedEdges, timelineYear, subjectName]);


    // --- 4. Initialize Network (Run once) ---
    useEffect(() => {
        if (containerRef.current && !networkRef.current) {
            const options = {
                nodes: {
                    shape: 'dot',
                    size: 20,
                    font: { size: 14, color: '#f1f5f9', face: 'arial', strokeWidth: 2, strokeColor: '#0f172a' }, // Slate-100 text, Slate-900 stroke
                    borderWidth: 2,
                    shadow: true
                },
                edges: {
                    width: 1,
                    color: { color: '#64748b', highlight: '#3b82f6', hover: '#3b82f6' }, // Slate-500
                    smooth: { type: 'continuous', roundness: 0.5 },
                    arrows: { to: { enabled: true, scaleFactor: 0.5 } },
                    font: { size: 10, align: 'middle', color: '#94a3b8', background: '#1e293b', strokeWidth: 0 } // Slate-400 text, Slate-800 bg
                },
                groups: {
                    person: { color: { background: '#1e3a8a', border: '#3b82f6' }, shape: 'icon', icon: { code: '\uf007', color: '#60a5fa', face: '"Font Awesome 6 Free"', weight: '900', size: 30 } }, // Dark blue bg
                    organization: { color: { background: '#052e16', border: '#22c55e' }, shape: 'icon', icon: { code: '\uf1ad', color: '#4ade80', face: '"Font Awesome 6 Free"', weight: '900', size: 30 } },
                    location: { color: { background: '#450a0a', border: '#ef4444' }, shape: 'icon', icon: { code: '\uf3c5', color: '#f87171', face: '"Font Awesome 6 Free"', weight: '900', size: 30 } },
                    event: { color: { background: '#422006', border: '#f59e0b' }, shape: 'icon', icon: { code: '\uf073', color: '#fbbf24', face: '"Font Awesome 6 Free"', weight: '900', size: 30 } },
                    product: { color: { background: '#312e81', border: '#6366f1' }, shape: 'icon', icon: { code: '\uf466', color: '#818cf8', face: '"Font Awesome 6 Free"', weight: '900', size: 30 } },
                    other: { color: { background: '#334155', border: '#94a3b8' }, shape: 'dot' }
                },
                physics: {
                    stabilization: true,
                    barnesHut: { gravitationalConstant: -10000, centralGravity: 0.3, springLength: 150, springConstant: 0.04, damping: 0.09, avoidOverlap: 0.2 },
                    solver: 'barnesHut'
                },
                interaction: { hover: true, tooltipDelay: 200, navigationButtons: true, keyboard: true },
                layout: { improvedLayout: true }
            };

            networkRef.current = new Network(containerRef.current, { nodes: [], edges: [] }, options);

            // Focus on subject logic (could be moved out, but keeping simplistic)
            networkRef.current.once("stabilizationIterationsDone", function () {
                // Focus logic can be triggered here if needed
            });
        }

        return () => {
            // Cleanup if needed, but we keep ref alive usually
            if (networkRef.current) {
                networkRef.current.destroy();
                networkRef.current = null;
            }
        };
    }, []); // Run ONCE


    // --- 5. Update Network Data ---
    useEffect(() => {
        if (networkRef.current) {
            // efficient update
            networkRef.current.setData(visibleData);

            // If it's the very first load or major change, we might want to fit/stabilize? 
            // Usually setData acts smoothly. 
        }
    }, [visibleData]);


    // --- 6. Playback Logic ---
    useEffect(() => {
        let interval;
        if (isPlaying) {
            interval = setInterval(() => {
                setTimelineYear(prev => {
                    if (prev >= yearRange.max) {
                        setIsPlaying(false);
                        return prev;
                    }
                    return prev + 1;
                });
            }, 1000); // 1 second per year
        }
        return () => clearInterval(interval);
    }, [isPlaying, yearRange.max]);


    return (
        <div className="flex flex-col border border-slate-700 rounded-lg bg-slate-900 shadow-sm overflow-hidden">
            <div className="relative w-full h-[600px]">
                <div ref={containerRef} className="w-full h-full bg-slate-950" />

                {/* Legend Overlay */}
                <div className="absolute top-4 right-4 bg-slate-900/90 backdrop-blur-sm p-3 rounded-lg shadow-md border border-slate-700 text-xs text-slate-300 pointer-events-none">
                    <div className="font-bold mb-2 text-slate-100">Legend</div>
                    <div className="flex items-center gap-2 mb-1"><span className="w-3 h-3 rounded-full bg-blue-900 border border-blue-500"></span> Person</div>
                    <div className="flex items-center gap-2 mb-1"><span className="w-3 h-3 rounded-full bg-green-900 border border-green-500"></span> Organization</div>
                    <div className="flex items-center gap-2 mb-1"><span className="w-3 h-3 rounded-full bg-red-900 border border-red-500"></span> Location</div>
                    <div className="flex items-center gap-2 mb-1"><span className="w-3 h-3 rounded-full bg-yellow-900 border border-yellow-500"></span> Event</div>
                    <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-indigo-900 border border-indigo-500"></span> Product</div>
                </div>

                {/* Timeline Overlay */}
                <div className="absolute bottom-6 left-6 right-6 bg-slate-900/95 backdrop-blur-md p-4 rounded-xl shadow-lg border border-slate-700 flex items-center gap-4 animate-fade-in z-10">
                    <button
                        onClick={() => setIsPlaying(!isPlaying)}
                        className="p-2 rounded-full bg-slate-800 text-blue-400 hover:bg-slate-700 transition-colors focus:outline-none border border-slate-700"
                    >
                        {isPlaying ? <Pause size={20} fill="currentColor" /> : <Play size={20} fill="currentColor" className="ml-0.5" />}
                    </button>

                    <div className="flex-grow">
                        <div className="flex justify-between text-xs font-bold text-slate-500 mb-1">
                            <span>{yearRange.min}</span>
                            <span className="text-blue-400 text-lg">{timelineYear || yearRange.max}</span>
                            <span>{yearRange.max}</span>
                        </div>
                        <input
                            type="range"
                            min={yearRange.min}
                            max={yearRange.max}
                            value={timelineYear || yearRange.max}
                            onChange={(e) => {
                                setIsPlaying(false);
                                setTimelineYear(parseInt(e.target.value));
                            }}
                            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
                        />
                    </div>
                    <button
                        onClick={() => { setIsPlaying(false); setTimelineYear(yearRange.min); }}
                        className="p-2 text-slate-500 hover:text-slate-300"
                        title="Reset to Start"
                    >
                        <SkipBack size={18} />
                    </button>
                    <button
                        onClick={() => { setIsPlaying(false); setTimelineYear(yearRange.max); }}
                        className="p-2 text-slate-500 hover:text-slate-300"
                        title="Jump to End"
                    >
                        <SkipForward size={18} />
                    </button>
                </div>
            </div>
        </div>
    );
};

export default GraphView;

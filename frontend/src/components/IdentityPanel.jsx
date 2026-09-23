import React from 'react';
import { User, MapPin, Briefcase, Calendar, Shield, CreditCard, Users, Hash } from 'lucide-react';

const IdentityPanel = ({ selectedCandidate, candidates, onSelectCandidate, subjectProfile }) => {

    // If no specific candidate logic is active, but we have form data, populate a "Target Profile" view
    // This makes the panel useful even before searching
    const displayProfile = selectedCandidate || {
        name: subjectProfile?.subjectName || 'New Subject',
        title: subjectProfile?.profession || 'N/A',
        organization: subjectProfile?.company || 'N/A',
        location: subjectProfile?.region || 'Global',
        // Mock data for visual completeness if it's just the form data
        riskScore: 'Pending',
        matchConfidence: 'Scanning...'
    };

    // If we have candidates (mock disambiguation), show list
    if (candidates && candidates.length > 0 && !selectedCandidate) {
        return (
            <div className="bg-slate-800/50 backdrop-blur-md border border-slate-700 rounded-xl p-4 h-full flex flex-col">
                <h3 className="text-white font-medium mb-3 flex items-center gap-2">
                    <Users size={16} className="text-blue-400" />
                    Select Identity ({candidates.length})
                </h3>
                <div className="overflow-y-auto space-y-3 pr-2 scrollbar-thin">
                    {candidates.map((cand, idx) => (
                        <div
                            key={idx}
                            onClick={() => onSelectCandidate(cand)}
                            className="bg-slate-900/50 p-3 rounded-lg border border-slate-700 hover:border-blue-500 cursor-pointer transition-colors group"
                        >
                            <div className="flex justify-between items-start">
                                <h4 className="text-sm font-bold text-slate-200 group-hover:text-blue-400">{cand.name}</h4>
                                <span className="text-xs text-slate-500">{cand.dob}</span>
                            </div>
                            <p className="text-xs text-slate-400 mt-1">{cand.role} at {cand.org}</p>
                        </div>
                    ))}
                </div>
            </div>
        );
    }

    return (
        <div className="bg-slate-800/50 backdrop-blur-md border border-slate-700 rounded-xl overflow-hidden shadow-lg h-full flex flex-col min-h-[250px]">
            <div className="bg-gradient-to-r from-blue-900/40 to-slate-900/40 p-6 border-b border-slate-700 flex items-center gap-5">
                <div className="w-16 h-16 rounded-full bg-slate-700 flex items-center justify-center border-2 border-slate-600 shadow-inner">
                    <User size={32} className="text-slate-400" />
                </div>
                <div>
                    <h2 className="text-xl font-bold text-white tracking-tight">{displayProfile.name}</h2>
                    <div className="flex items-center gap-2 text-slate-400 text-sm mt-1">
                        <Briefcase size={14} />
                        <span>{displayProfile.title}</span>
                        <span className="text-slate-600">•</span>
                        <span>{displayProfile.organization}</span>
                    </div>
                </div>
            </div>

            <div className="p-6 grid grid-cols-2 gap-y-6 gap-x-4">
                <DetailItem icon={<MapPin size={16} />} label="Location" value={displayProfile.location} />
                <DetailItem icon={<Calendar size={16} />} label="Age / DOB" value={subjectProfile?.age || subjectProfile?.dob || 'Unknown'} />
                <DetailItem icon={<Users size={16} />} label="Associates" value={subjectProfile?.spouse ? `Spouse: ${subjectProfile.spouse}` : displayProfile.associates || 'None Known'} />
                <DetailItem icon={<Hash size={16} />} label="Ownership" value={subjectProfile?.ownership || 'N/A'} />
            </div>

            <div className="mt-auto p-4 bg-slate-900/30 border-t border-slate-800 flex justify-between items-center">
                <div className="text-xs text-slate-500 uppercase font-semibold tracking-wider">Analysis Status</div>
                <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse"></span>
                    <span className="text-xs font-mono text-blue-400">ACTIVE</span>
                </div>
            </div>
        </div>
    );
};

const DetailItem = ({ icon, label, value }) => (
    <div className="flex items-start gap-3">
        <div className="mt-0.5 text-slate-500">{icon}</div>
        <div>
            <div className="text-xs font-bold text-slate-500 uppercase mb-0.5">{label}</div>
            <div className="text-sm text-slate-200 font-medium">{value}</div>
        </div>
    </div>
);

export default IdentityPanel;

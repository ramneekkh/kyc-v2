import React, { useState } from 'react';
import { Search, Sliders, Calendar, Globe, Briefcase, Building, ChevronDown, ChevronUp } from 'lucide-react';

const SearchArea = ({
    onSearch,
    isSearching,
    searchMode,
    setSearchMode,
    savedSubjects,
    onLoadSubject,
    loadingSubjects,
    initialFormData
}) => {
    const [formData, setFormData] = useState(initialFormData || {
        subjectName: '',
        profession: '',
        company: '',
        region: 'Global',
        dob: '',
        age: '',
        ownership: '',
        spouse: '',
        alias: ''
    });
    const [keywordInput, setKeywordInput] = useState('');
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [selectedSubjectId, setSelectedSubjectId] = useState('');

    const handleInputChange = (e) => {
        const { name, value } = e.target;
        setFormData(prev => ({
            ...prev,
            [name]: value
        }));
    };

    const handleSearchClick = () => {
        if (!formData.subjectName.trim()) return;

        // Convert comma-separated string to list
        const customKeywords = keywordInput
            ? keywordInput.split(',').map(s => s.trim()).filter(s => s.length > 0)
            : [];

        onSearch(formData, customKeywords);
    };

    const handleLoadClick = () => {
        if (selectedSubjectId) {
            onLoadSubject(selectedSubjectId);
        }
    };

    return (
        <div className="bg-slate-800/50 backdrop-blur-md border border-slate-700 rounded-xl p-5 mb-6 shadow-sm">
            <div className="flex items-center justify-between mb-4">
                <h2 className="text-white text-lg font-semibold flex items-center gap-2">
                    <Search size={20} className="text-blue-400" />
                    Identity Search
                </h2>

                {/* Mode Toggle */}
                <div className="bg-slate-900/80 p-1 rounded-lg flex border border-slate-700">
                    <button
                        onClick={() => setSearchMode('new')}
                        className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-all ${searchMode === 'new' ? 'bg-blue-600 text-white shadow-sm' : 'text-slate-400 hover:text-slate-200'}`}
                    >
                        New Search
                    </button>
                    <button
                        onClick={() => setSearchMode('existing')}
                        className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-all ${searchMode === 'existing' ? 'bg-blue-600 text-white shadow-sm' : 'text-slate-400 hover:text-slate-200'}`}
                    >
                        Load Record
                    </button>
                </div>
            </div>

            {searchMode === 'new' ? (
                <div className="flex flex-col gap-4">
                    <div className="flex gap-4">
                        <div className="relative flex-grow">
                            <label className="block text-xs font-medium text-slate-400 mb-1 ml-1">Subject Name <span className="text-red-400">*</span></label>
                            <input
                                type="text"
                                name="subjectName"
                                value={formData.subjectName}
                                onChange={handleInputChange}
                                placeholder="Enter full name..."
                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white placeholder-slate-500 focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all"
                            />
                        </div>
                        <div className="relative flex-grow">
                            <label className="block text-xs font-medium text-slate-400 mb-1 ml-1">Context / Keywords</label>
                            <input
                                type="text"
                                value={keywordInput}
                                onChange={(e) => setKeywordInput(e.target.value)}
                                onKeyDown={(e) => e.key === 'Enter' && handleSearchClick()}
                                placeholder="Fraud, Corruption, CEO..."
                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white placeholder-slate-500 focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all"
                            />
                        </div>
                        <div className="flex items-end">
                            <button
                                onClick={handleSearchClick}
                                disabled={isSearching || !formData.subjectName.trim()}
                                className="bg-blue-600 hover:bg-blue-500 disabled:bg-slate-700 disabled:text-slate-500 text-white px-6 py-2.5 rounded-lg font-semibold transition-colors flex items-center gap-2 h-[42px]"
                            >
                                {isSearching ? (
                                    <span className="animate-spin h-4 w-4 border-2 border-white border-t-transparent rounded-full"></span>
                                ) : (
                                    <Search size={18} />
                                )}
                                Investigate
                            </button>
                        </div>
                    </div>

                    <div className="border-t border-slate-700 pt-2">
                        <button
                            onClick={() => setShowAdvanced(!showAdvanced)}
                            className="flex items-center gap-2 text-xs font-medium text-blue-400 hover:text-blue-300 transition-colors"
                        >
                            <Sliders size={14} />
                            {showAdvanced ? 'Hide Advanced Attributes' : 'Add Advanced Attributes (Job, DOB, Region...)'}
                            {showAdvanced ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                        </button>

                        {showAdvanced && (
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4 animate-fade-in p-2">
                                <AttributeInput icon={<Briefcase size={14} />} label="Profession" name="profession" value={formData.profession} onChange={handleInputChange} />
                                <AttributeInput icon={<Building size={14} />} label="Company" name="company" value={formData.company} onChange={handleInputChange} />
                                <AttributeInput icon={<Globe size={14} />} label="Region" name="region" value={formData.region} onChange={handleInputChange} />
                                <AttributeInput icon={<Calendar size={14} />} label="Year of Birth" name="dob" value={formData.dob} onChange={handleInputChange} />
                                <AttributeInput icon={<Calendar size={14} />} label="Age" name="age" value={formData.age} onChange={handleInputChange} placeholder="e.g. 45" />
                            </div>
                        )}
                    </div>
                </div>
            ) : (
                <div className="animate-fade-in">
                    <label className="block text-xs font-medium text-slate-400 mb-1 ml-1">Select Saved Subject</label>
                    <div className="flex gap-4">
                        <select
                            value={selectedSubjectId}
                            onChange={(e) => setSelectedSubjectId(e.target.value)}
                            className="flex-grow bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all"
                        >
                            <option value="">-- Choose from History --</option>
                            {savedSubjects && savedSubjects.map(subj => (
                                <option key={subj.subject_id} value={subj.subject_id}>
                                    {subj.name} ({new Date(subj.created_at || Date.now()).toLocaleDateString()})
                                </option>
                            ))}
                        </select>
                        <button
                            onClick={handleLoadClick}
                            disabled={!selectedSubjectId || isSearching}
                            className="bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-700 disabled:text-slate-500 text-white px-6 py-2.5 rounded-lg font-semibold transition-colors flex items-center gap-2"
                        >
                            {isSearching ? <span className="animate-spin h-4 w-4 border-2 border-white border-t-transparent rounded-full"></span> : null}
                            Load Record
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
};

const AttributeInput = ({ icon, label, name, value, onChange, placeholder }) => (
    <div>
        <label className="flex items-center gap-1.5 text-xs font-medium text-slate-400 mb-1">
            {icon} {label}
        </label>
        <input
            type="text"
            name={name}
            value={value}
            onChange={onChange}
            placeholder={placeholder || "Optional"}
            className="w-full bg-slate-900 border border-slate-700 rounded-md px-3 py-1.5 text-sm text-white placeholder-slate-600 focus:border-blue-500 outline-none"
        />
    </div>
);

export default SearchArea;

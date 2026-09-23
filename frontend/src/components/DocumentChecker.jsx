import React, { useState, useCallback } from 'react';
import { Upload, FileText, CheckCircle, AlertTriangle, X, Loader } from 'lucide-react';

const DocumentChecker = ({ subjectId, subjectName, onProfileUpdate }) => {
    const [file, setFile] = useState(null);
    const [analyzing, setAnalyzing] = useState(false);
    const [result, setResult] = useState(null);
    const [dragActive, setDragActive] = useState(false);

    const handleDrag = (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (e.type === "dragenter" || e.type === "dragover") {
            setDragActive(true);
        } else if (e.type === "dragleave") {
            setDragActive(false);
        }
    };

    const handleDrop = (e) => {
        e.preventDefault();
        e.stopPropagation();
        setDragActive(false);
        if (e.dataTransfer.files && e.dataTransfer.files[0]) {
            handleFileSelect(e.dataTransfer.files[0]);
        }
    };

    const handleFileSelect = (selectedFile) => {
        if (selectedFile.type.includes('pdf') || selectedFile.type.includes('image')) {
            setFile(selectedFile);
            setResult(null); // Reset previous result
        } else {
            alert("Only PDF or Image files are supported.");
        }
    };

    const uploadAndAnalyze = async () => {
        if (!file || !subjectId) return;

        setAnalyzing(true);
        const formData = new FormData();
        formData.append('file', file);
        formData.append('subjectId', subjectId);
        formData.append('subjectName', subjectName);

        try {
            const response = await fetch('/api/upload-document', {
                method: 'POST',
                body: formData,
            });

            if (!response.ok) throw new Error('Analysis failed');

            const data = await response.json();
            setResult(data);

            if (data.profile_augmented && onProfileUpdate) {
                // Ideally refresh profile or notify user
                onProfileUpdate();
            }
        } catch (error) {
            console.error("Analysis Error:", error);
            alert("Analysis failed. Please try again.");
        } finally {
            setAnalyzing(false);
        }
    };

    const clearFile = () => {
        setFile(null);
        setResult(null);
    };

    // Render Authenticity Score Badge
    const ScoreBadge = ({ score }) => {
        const colors = {
            'High': 'bg-green-100 text-green-800 border-green-200',
            'Medium': 'bg-yellow-100 text-yellow-800 border-yellow-200',
            'Low': 'bg-red-100 text-red-800 border-red-200'
        };
        const color = colors[score] || 'bg-gray-100 text-gray-800';
        return <span className={`px-2 py-0.5 rounded-full text-xs font-bold border ${color}`}>{score} Confidence</span>;
    };

    return (
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-sm h-full flex flex-col">
            <h3 className="text-slate-100 font-bold mb-4 flex items-center gap-2">
                <FileText size={18} className="text-blue-500" />
                Document Checker
            </h3>

            {/* Upload Area */}
            {!file && !result && (
                <div
                    className={`flex-grow border-2 border-dashed rounded-lg flex flex-col items-center justify-center p-6 transition-colors ${dragActive ? 'border-blue-500 bg-blue-900/10' : 'border-slate-700 hover:border-slate-600'}`}
                    onDragEnter={handleDrag} onDragLeave={handleDrag} onDragOver={handleDrag} onDrop={handleDrop}
                >
                    <Upload size={32} className="text-slate-500 mb-2" />
                    <p className="text-sm text-slate-400 text-center mb-4">Drag & Drop ID, Bank Statement or Official Doc</p>
                    <label className="cursor-pointer bg-slate-800 hover:bg-slate-700 text-slate-300 font-semibold py-2 px-4 rounded border border-slate-700 text-xs transition-colors">
                        Select File
                        <input type="file" className="hidden" onChange={(e) => handleFileSelect(e.target.files[0])} accept=".pdf,image/*" />
                    </label>
                </div>
            )}

            {/* File Preview & Actions */}
            {file && !result && (
                <div className="flex-grow flex flex-col items-center justify-center p-4">
                    <div className="w-16 h-16 bg-slate-800 rounded-lg flex items-center justify-center mb-3">
                        <FileText size={32} className="text-blue-400" />
                    </div>
                    <p className="text-slate-200 font-medium text-sm truncate max-w-full mb-1">{file.name}</p>
                    <p className="text-slate-500 text-xs mb-6">{(file.size / 1024 / 1024).toFixed(2)} MB</p>

                    <div className="flex gap-2 w-full">
                        <button onClick={clearFile} className="flex-1 py-2 rounded-lg bg-slate-800 text-slate-400 text-xs font-bold hover:bg-slate-700">Cancel</button>
                        <button
                            onClick={uploadAndAnalyze}
                            disabled={analyzing}
                            className="flex-1 py-2 rounded-lg bg-blue-600 text-white text-xs font-bold hover:bg-blue-500 flex items-center justify-center gap-2"
                        >
                            {analyzing ? <Loader size={14} className="animate-spin" /> : <CheckCircle size={14} />}
                            {analyzing ? "Analyzing..." : "Validate"}
                        </button>
                    </div>
                </div>
            )}

            {/* Results View */}
            {result && (
                <div className="flex-grow overflow-y-auto space-y-4 pr-1 scrollbar-thin scrollbar-thumb-slate-700">
                    <div className="flex justify-between items-start">
                        <div>
                            <p className="text-xs text-slate-500 uppercase font-bold">Document Type</p>
                            <p className="text-sm font-semibold text-slate-200">{result.document_type || "Unknown"}</p>
                        </div>
                        <ScoreBadge score={result.is_authentic_score} />
                    </div>

                    {/* Red Flags */}
                    {result.red_flags && result.red_flags.length > 0 ? (
                        <div className="bg-red-900/20 border border-red-900/50 rounded-lg p-3">
                            <p className="text-xs font-bold text-red-400 flex items-center gap-1 mb-2">
                                <AlertTriangle size={12} /> Irregularities Detected
                            </p>
                            <ul className="list-disc list-inside text-xs text-red-300 space-y-1">
                                {result.red_flags.map((flag, i) => <li key={i}>{flag}</li>)}
                            </ul>
                        </div>
                    ) : (
                        <div className="bg-green-900/20 border border-green-900/50 rounded-lg p-3 flex items-center gap-2">
                            <CheckCircle size={14} className="text-green-400" />
                            <span className="text-xs text-green-300">No content irregularities detected.</span>
                        </div>
                    )}

                    {/* Extracted Data */}
                    {result.extracted_data && (
                        <div>
                            <p className="text-xs text-slate-500 uppercase font-bold mb-2">Extracted Intelligence</p>
                            <div className="bg-slate-950 rounded-lg p-3 space-y-2 border border-slate-800">
                                {Object.entries(result.extracted_data).map(([key, val]) => (
                                    <div key={key}>
                                        <span className="text-xs text-slate-500 capitalize">{key.replace('_', ' ')}:</span>
                                        <p className="text-xs text-slate-300 font-mono">
                                            {Array.isArray(val) ? val.join(", ") : val}
                                        </p>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    <div className="pt-2">
                        <button onClick={clearFile} className="w-full py-2 bg-slate-800 text-slate-400 text-xs rounded hover:text-white transition-colors">
                            Analyze Another Document
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
};

export default DocumentChecker;

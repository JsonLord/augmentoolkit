import React, { createContext, useState, useContext, useCallback, useRef } from 'react';
import { fetchInputStructure, fetchOutputStructure } from '../api'; // Assuming API functions exist

const FileManagerContext = createContext();

export const useFileManager = () => useContext(FileManagerContext);

/**
 * Adapts API data structure to the structure expected by react-arborist.
 * Sorts directories first, then alphabetically.
 * @param {Array<object>} apiData - Data from the API
 * @returns {Array<object>} Adapted and sorted data
 */
const adaptAndSortApiData = (apiData) => {
    if (!Array.isArray(apiData)) return [];
    const adapted = apiData.map(item => ({
        id: item.path,
        name: item.path.split('/').pop() || item.path,
        relativePath: item.path,
        is_dir: item.is_dir,
        children: item.is_dir ? (item.children ? adaptAndSortApiData(item.children) : null) : undefined,
    }));
    // Sort: directories first, then alphabetically
    adapted.sort((a, b) => {
        if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
        return a.name.localeCompare(b.name);
    });
    return adapted;
};


export const FileManagerProvider = ({ children }) => {
    const fileEpoch = useRef(0);
    // --- Input State ---
    const [inputTreeData, setInputTreeData] = useState(null);
    const [isInputLoading, setIsInputLoading] = useState(false);
    const [inputError, setInputError] = useState(null);

    // --- Output State ---
    const [outputTreeData, setOutputTreeData] = useState(null);
    const [isOutputLoading, setIsOutputLoading] = useState(false);
    const [outputError, setOutputError] = useState(null);

    // --- Fetch Input Data (with caching) ---
    const fetchInputData = useCallback(async (forceRefresh = false) => {
        const epoch = fileEpoch.current;
        if (inputTreeData && !forceRefresh) {
            // console.log("Using cached input data");
            return; // Use cached data if available and not forcing refresh
        }
        // console.log("Fetching input data...", { forceRefresh });
        setIsInputLoading(true);
        setInputError(null);
        try {
            const structure = await fetchInputStructure();
            if (epoch !== fileEpoch.current) return;
            const adaptedData = adaptAndSortApiData(structure);
            setInputTreeData(adaptedData);
        } catch (e) {
            if (epoch !== fileEpoch.current) return;
            console.error("Failed to fetch input structure:", e);
            setInputError(e.message || 'Failed to load input files structure.');
            setInputTreeData([]); // Set empty on error to stop loading indicators
        } finally {
            if (epoch === fileEpoch.current) setIsInputLoading(false);
        }
    }, [inputTreeData]); // Re-run if cached data changes (relevant for forceRefresh logic)

    // --- Fetch Output Data (with caching) ---
    const fetchOutputData = useCallback(async (forceRefresh = false) => {
        const epoch = fileEpoch.current;
        if (outputTreeData && !forceRefresh) {
            // console.log("Using cached output data");
            return; // Use cached data
        }
        // console.log("Fetching output data...", { forceRefresh });
        setIsOutputLoading(true);
        setOutputError(null);
        try {
            const structure = await fetchOutputStructure();
            if (epoch !== fileEpoch.current) return;
            const adaptedData = adaptAndSortApiData(structure);
            setOutputTreeData(adaptedData);
        } catch (e) {
            if (epoch !== fileEpoch.current) return;
            console.error("Failed to fetch output structure:", e);
            setOutputError(e.message || 'Failed to load output files structure.');
            setOutputTreeData([]); // Set empty on error
        } finally {
            if (epoch === fileEpoch.current) setIsOutputLoading(false);
        }
    }, [outputTreeData]); // Re-run if cached data changes

    // --- Refresh Functions ---
    // These simply call the fetch functions with forceRefresh=true
    const refreshInputData = useCallback(() => fetchInputData(true), [fetchInputData]);
    const refreshOutputData = useCallback(() => fetchOutputData(true), [fetchOutputData]);


    const clearFileData = useCallback(() => {
        fileEpoch.current += 1;
        setInputTreeData(null); setOutputTreeData(null);
        setInputError(null); setOutputError(null);
        setIsInputLoading(false); setIsOutputLoading(false);
    }, []);

    const value = {
        clearFileData,
        inputTreeData,
        isInputLoading,
        inputError,
        fetchInputData,
        refreshInputData,

        outputTreeData,
        isOutputLoading,
        outputError,
        fetchOutputData,
        refreshOutputData,
    };

    return (
        <FileManagerContext.Provider value={value}>
            {children}
        </FileManagerContext.Provider>
    );
}; 
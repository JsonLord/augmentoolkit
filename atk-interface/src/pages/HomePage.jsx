import { useEffect } from 'react';
import { useNavigation } from '../context/NavigationContext';

function HomePage() {
  const { setHighlightedNavLink } = useNavigation();

  useEffect(() => {
    setHighlightedNavLink('/configs'); // highlight configs when mounted
    return () => setHighlightedNavLink(null); // clear on unmount
  }, [setHighlightedNavLink]);

  return (
    <div className="p-8 flex flex-col items-center justify-center h-full">
      <h1 className="text-4xl font-bold mb-4">Welcome to Augmentoolkit</h1>
      <h2>D0 dataset preparation pilot</h2>
      <ol className="list-decimal p-6 text-left">
        <li>Unlock operator access using the operator token.</li>
        <li>Open Configs, select pilot-preparation-config, create a private YAML copy and save it.</li>
        <li>Open Inputs and upload a small public or synthetic UTF-8 TXT (at most 1 MiB).</li>
        <li>Select that file, choose your private config, confirm D0 and click Prepare Dataset.</li>
        <li>Follow the job on Monitor. Preview and download the preparation JSONL and manifest.</li>
        <li>Reopen Outputs to retrieve completed artifacts; the browser need not stay open.</li>
      </ol>
      <p>Local preparation must be enabled by the operator. Storage is ephemeral until persistence is separately qualified.</p>
      <p>Native LLM generation, Chat, advanced training and legacy pipeline execution: Not available.</p>
      {/* Placeholder for logo or other content */}
    </div>
  );
}

export default HomePage; 
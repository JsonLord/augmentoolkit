import PreparationPanel from './components/PreparationPanel';
import { Routes, Route } from 'react-router-dom';
import { TaskHistoryProvider } from './context/TaskHistoryContext';
import { FileManagerProvider } from './context/FileManagerContext';
import Layout from './components/Layout';
import HomePage from './pages/HomePage';
import ConfigSelector from './pages/ConfigSelector';
import ConfigEditor from './pages/ConfigEditor';
import InputManager from './pages/InputManager';
import OutputManager from './pages/OutputManager';
import PipelineMonitor from './pages/PipelineMonitor';
import NotFound from './pages/NotFound';
import LogManager from './pages/LogManager';
import LogViewer from './pages/LogViewer';
// import './App.css'; // Keep or remove based on whether it has useful base styles

function App() {
  return (
    <TaskHistoryProvider>
      <FileManagerProvider>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<HomePage />} /> {/* Default route is now HomePage */}
            <Route path="configs" element={<ConfigSelector />} /> {/* Route for selecting configs */}
            <Route path="configs/:configId" element={<ConfigEditor />} /> {/* Route for editing a specific config */}
            <Route path="inputs" element={<InputManager />} />
            <Route path="outputs" element={<><PreparationPanel mode="outputs" /><p>Legacy output actions: Not available. Use the preparation artifacts above.</p><div inert className="opacity-50"><OutputManager /></div></>} />
            <Route path="monitor" element={<><PreparationPanel /><p>Legacy Huey execution: Not available.</p><div inert className="opacity-50"><PipelineMonitor /></div></>} />
            <Route path="logs" element={<LogManager />} />
            <Route path="logs/:taskId" element={<LogViewer />} />
            <Route path="chat" element={<p className="p-8">Chat / LLM server: Not available in this pilot.</p>} />
            <Route path="*" element={<NotFound />} /> {/* Catch-all for 404 */}
          </Route>
        </Routes>
      </FileManagerProvider>
    </TaskHistoryProvider>
  );
}

export default App;

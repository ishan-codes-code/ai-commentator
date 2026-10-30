import { createRoot } from 'react-dom/client';
import Hello from './components/Hello';
import WorkerStatus from './components/WorkerStatus';
import './index.css';

const App = () =>{
    return (
        <>
        <Hello/>
        <WorkerStatus/>
        </>
    )
};

const container = document.getElementById("root");
const root = createRoot(container);
root.render(<App/>);

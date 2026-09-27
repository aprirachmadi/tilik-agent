import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Workspace } from "./pages/Workspace";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Workspace />} />
        <Route path="/c/:conversationId" element={<Workspace />} />
      </Routes>
    </BrowserRouter>
  );
}

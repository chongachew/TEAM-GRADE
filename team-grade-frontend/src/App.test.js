import { render, screen } from '@testing-library/react';
import App from './App';

test('renders the ingest form on load', () => {
  render(<App />);
  expect(screen.getByText(/TEAM-GRADE/i)).toBeInTheDocument();
  expect(screen.getByPlaceholderText(/youtube.com\/watch/i)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /submit for analysis/i })).toBeInTheDocument();
});

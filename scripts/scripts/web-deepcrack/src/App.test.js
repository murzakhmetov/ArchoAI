import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from './App';

const pickFile = (container) => {
  const file = new File(['pixels'], 'crack.png', { type: 'image/png' });
  fireEvent.change(container.querySelector('input[type="file"]'), { target: { files: [file] } });
};

const predict = () => fireEvent.click(screen.getByRole('button', { name: /predict/i }));

const answer = (ok, status, body) => ({ ok, status, json: () => Promise.resolve(body) });

beforeEach(() => {
  URL.createObjectURL = jest.fn(() => 'blob:preview');
  global.fetch = jest.fn();
  jest.spyOn(console, 'log').mockImplementation(() => {});
});

afterEach(() => {
  jest.restoreAllMocks();
  delete global.fetch;
});

test('renders the upload form', () => {
  render(<App />);
  expect(screen.getByRole('heading', { name: /crack detection using deepcrack/i })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /predict/i })).toBeInTheDocument();
});

test('predicting without a file asks for one and sends nothing', () => {
  render(<App />);
  predict();
  expect(screen.getByRole('alert')).toHaveTextContent(/choose an image/i);
  expect(global.fetch).not.toHaveBeenCalled();
});

test('shows the images returned by the server', async () => {
  global.fetch.mockResolvedValue(answer(true, 200, ['a', 'b', 'c', 'd', 'e', 'f', 'g']));
  const { container } = render(<App />);
  pickFile(container);
  predict();

  expect(global.fetch).toHaveBeenCalledWith('http://127.0.0.1:8000/predict/1-1/m', expect.anything());
  const sources = () => Array.from(container.querySelectorAll('img'), (img) => img.getAttribute('src'));
  await waitFor(() => expect(sources()).toContain('data:image/png;base64,g'));
  expect(sources()).toContain('data:image/png;base64,a');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('shows the server message instead of broken images when the request is refused', async () => {
  global.fetch.mockResolvedValue(answer(false, 422, { detail: 'unit must be 1-8 Latin letters or digits' }));
  const { container } = render(<App />);
  pickFile(container);
  predict();

  expect(await screen.findByRole('alert')).toHaveTextContent('unit must be 1-8 Latin letters');
  const broken = Array.from(container.querySelectorAll('img'))
    .filter((img) => (img.getAttribute('src') || '').includes('undefined'));
  expect(broken).toHaveLength(0);
});

test('tells the user when the server cannot be reached', async () => {
  global.fetch.mockRejectedValue(new TypeError('Failed to fetch'));
  const { container } = render(<App />);
  pickFile(container);
  predict();
  expect(await screen.findByRole('alert')).toHaveTextContent(/failed to fetch/i);
});

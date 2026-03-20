import express from 'express';
import bodyParser from 'body-parser';
import admin from 'firebase-admin';
import dotenv from 'dotenv';
import { spawn } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';
import fs from 'fs';

dotenv.config();

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Initialize Firebase Admin
const serviceAccountPath = path.join(__dirname, 'serviceAccountKey.json');
const serviceAccount = JSON.parse(fs.readFileSync(serviceAccountPath, 'utf8'));
admin.initializeApp({
  credential: admin.credential.cert(serviceAccount)
});

const db = admin.firestore();
const app = express();
app.use(bodyParser.json());

// Health check endpoint
app.get('/health', (req, res) => {
  res.json({ status: 'ok' });
});

// Firestore connectivity test endpoint
app.get('/check-firestore', async (req, res) => {
  try {
    console.log('[Firestore Test] Checking connectivity...');
    const testData = {
      test: true,
      timestamp: new Date().toISOString(),
      message: 'Firestore connectivity test'
    };
    const docRef = await db.collection('firestore_test').add(testData);
    console.log('[Firestore Test] Successfully wrote test document:', docRef.id);
    res.json({
      status: 'ok',
      message: 'Firestore is connected and working',
      docId: docRef.id,
      timestamp: testData.timestamp
    });
  } catch (err) {
    console.error('[Firestore Test] Failed:', err.code, err.message);
    res.status(500).json({
      status: 'error',
      message: 'Firestore connection failed',
      error: err.message,
      code: err.code
    });
  }
});

// Helper function to call Python backend
async function callPythonEngine(poseData, level, program, position) {
  return new Promise((resolve, reject) => {
    const apiDir = path.join(__dirname, '..');
    const pythonExe = path.join(__dirname, '..', '..', '.venv', 'Scripts', 'python.exe');
    const mainPyPath = path.join(apiDir, 'main.py');
    
    const python = spawn(pythonExe, [
      mainPyPath,
      '--pose_data', JSON.stringify(poseData),
      '--level', level,
      '--program', program,
      '--position', position
    ], {
      cwd: apiDir,
      shell: false
    });

    let output = '';
    let errorOutput = '';

    python.stdout.on('data', (data) => {
      output += data.toString();
    });

    python.stderr.on('data', (data) => {
      errorOutput += data.toString();
    });

    python.on('close', (code) => {
      if (code !== 0) {
        reject(new Error(`Python process exited with code ${code}: ${errorOutput}`));
      } else {
        try {
          resolve(JSON.parse(output));
        } catch (err) {
          reject(new Error(`Failed to parse Python output: ${err.message}`));
        }
      }
    });
  });
}

// Process rep endpoint
app.post('/process', async (req, res) => {
  try {
    const { pose_data, level, program, position } = req.body;

    // Validate inputs
    if (!pose_data || !level || !program || !position) {
      return res.status(400).json({ 
        error: 'Missing required fields: pose_data, level, program, position' 
      });
    }

    // Call Python backend
    const traits = await callPythonEngine(pose_data, level, program, position);

    const result = {
      position,
      level,
      program,
      traits,
      timestamp: new Date().toISOString(),
      status: 'success'
    };

    // Save to Firestore (with detailed logging)
    try {
      console.log('[Firestore] Attempting to save result:', JSON.stringify(result));
      const docRef = await db.collection('results').add(result);
      console.log('✓ [Firestore] Successfully saved with ID:', docRef.id);
    } catch (firestoreErr) {
      console.error('✗ [Firestore] Save failed:', firestoreErr.code, firestoreErr.message);
      console.error('[Firestore] Full error:', firestoreErr);
      // Continue anyway - return result to client
    }

    res.json(result);
  } catch (err) {
    console.error('Processing error:', err);
    res.status(500).json({ error: 'Processing failed', message: err.message });
  }
});

// Get results endpoint
app.get('/results/:id', async (req, res) => {
  try {
    const doc = await db.collection('results').doc(req.params.id).get();
    if (!doc.exists) {
      return res.status(404).json({ error: 'Result not found' });
    }
    res.json(doc.data());
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch result', message: err.message });
  }
});

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => {
  console.log(`Trench Engine API running on http://localhost:${PORT}`);
});
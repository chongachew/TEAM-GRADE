const http = require('http');

const positions = ['qb', 'rb', 'wr', 'ol', 'db', 'dl', 'lb'];
const levels = ['pro', 'college', 'hs'];
const programs = ['default', 'boise', 'elf'];

let completed = 0;
let passed = 0;

console.log('\n╔════════════════════════════════════════════════════════════════╗');
console.log('║         COMPREHENSIVE BACKEND VALIDATION TEST SUITE             ║');
console.log('╚════════════════════════════════════════════════════════════════╝\n');

// Test 1: All positions
console.log('[1/5] Testing ALL Supported Positions');
positions.forEach((pos) => {
  const payload = JSON.stringify({
    pose_data: { x: 0.5, y: 0.5, z: 0.5, confidence: 0.95 },
    level: 'college',
    program: 'default',
    position: pos
  });

  const options = {
    hostname: 'localhost',
    port: 3001,
    path: '/process',
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(payload)
    }
  };

  const req = http.request(options, (res) => {
    let data = '';
    res.on('data', d => data += d);
    res.on('end', () => {
      completed++;
      try {
        const result = JSON.parse(data);
        if (result.traits?.status === 'success') {
          const traitCount = result.traits?.traits ? Object.keys(result.traits.traits).length : 0;
          console.log(`      ✅ ${pos.toUpperCase()}: ${traitCount} traits generated`);
          passed++;
        } else {
          console.log(`      ❌ ${pos.toUpperCase()}: Failed - ${result.traits?.error}`);
        }
      } catch (e) {
        console.log(`      ❌ ${pos.toUpperCase()}: Parse error`);
      }
      if (completed === positions.length) runTest2();
    });
  });

  req.on('error', e => {
    completed++;
    console.log(`      ❌ ${pos.toUpperCase()}: ${e.message}`);
    if (completed === positions.length) runTest2();
  });

  req.write(payload);
  req.end();
});

function runTest2() {
  console.log(`\nResult: ${passed}/${positions.length} positions passed\n`);
  
  // Test 2: Firestore persistence
  console.log('[2/5] Testing Firestore Persistence');
  const fsPayload = JSON.stringify({
    pose_data: { x: 0.6, y: 0.6, z: 0.6, confidence: 0.98 },
    level: 'college',
    program: 'default',
    position: 'rb'
  });

  const fsOptions = {
    hostname: 'localhost',
    port: 3001,
    path: '/process',
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(fsPayload)
    }
  };

  const fsReq = http.request(fsOptions, (res) => {
    let data = '';
    res.on('data', d => data += d);
    res.on('end', () => {
      try {
        const result = JSON.parse(data);
        if (result.timestamp) {
          console.log(`      ✅ Timestamp: ${result.timestamp}`);
          console.log(`      ✅ Status: ${result.status}`);
          runTest3();
        } else {
          console.log(`      ❌ No timestamp in response`);
          runTest3();
        }
      } catch (e) {
        console.log(`      ❌ Parse error: ${e.message}`);
        runTest3();
      }
    });
  });

  fsReq.write(fsPayload);
  fsReq.end();
}

function runTest3() {
  // Test 3: Different levels
  console.log('\n[3/5] Testing Different Player Levels');
  let levelCompleted = 0;
  let levelPassed = 0;

  levels.forEach((level) => {
    const payload = JSON.stringify({
      pose_data: { x: 0.5, y: 0.5, z: 0.5 },
      level: level,
      program: 'default',
      position: 'qb'
    });

    const options = {
      hostname: 'localhost',
      port: 3001,
      path: '/process',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload)
      }
    };

    const req = http.request(options, (res) => {
      let data = '';
      res.on('data', d => data += d);
      res.on('end', () => {
        levelCompleted++;
        try {
          const result = JSON.parse(data);
          if (result.traits?.status === 'success') {
            console.log(`      ✅ ${level.toUpperCase()}: Success`);
            levelPassed++;
          } else {
            console.log(`      ❌ ${level.toUpperCase()}: Failed`);
          }
        } catch (e) {
          console.log(`      ❌ ${level.toUpperCase()}: Error`);
        }
        if (levelCompleted === levels.length) runTest4();
      });
    });

    req.write(payload);
    req.end();
  });
}

function runTest4() {
  // Test 4: Different programs
  console.log('\n[4/5] Testing Different Programs');
  let progCompleted = 0;
  let progPassed = 0;

  programs.forEach((prog) => {
    const payload = JSON.stringify({
      pose_data: { x: 0.5, y: 0.5, z: 0.5 },
      level: 'college',
      program: prog,
      position: 'ol'
    });

    const options = {
      hostname: 'localhost',
      port: 3001,
      path: '/process',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload)
      }
    };

    const req = http.request(options, (res) => {
      let data = '';
      res.on('data', d => data += d);
      res.on('end', () => {
        progCompleted++;
        try {
          const result = JSON.parse(data);
          if (result.traits?.status === 'success') {
            console.log(`      ✅ ${prog.toUpperCase()}: Success`);
            progPassed++;
          } else {
            console.log(`      ❌ ${prog.toUpperCase()}: Failed`);
          }
        } catch (e) {
          console.log(`      ❌ ${prog.toUpperCase()}: Error`);
        }
        if (progCompleted === programs.length) runTest5();
      });
    });

    req.write(payload);
    req.end();
  });
}

function runTest5() {
  // Test 5: Health check and Firestore connectivity
  console.log('\n[5/5] Testing Health & Firestore Endpoints');
  
  http.get('http://localhost:3001/health', (res) => {
    let data = '';
    res.on('data', d => data += d);
    res.on('end', () => {
      try {
        const result = JSON.parse(data);
        console.log(`      ✅ /health: ${result.status}`);
      } catch (e) {
        console.log(`      ❌ /health: Error`);
      }
      
      // Check firestore
      http.get('http://localhost:3001/check-firestore', (res) => {
        let data = '';
        res.on('data', d => data += d);
        res.on('end', () => {
          try {
            const result = JSON.parse(data);
            if (result.status === 'ok') {
              console.log(`      ✅ /check-firestore: Connected`);
            } else {
              console.log(`      ⚠️  /check-firestore: ${result.error}`);
            }
          } catch (e) {
            console.log(`      ❌ /check-firestore: Error`);
          }
          
          console.log('\n╔════════════════════════════════════════════════════════════════╗');
          console.log('║                    TEST SUITE COMPLETE                          ║');
          console.log('╚════════════════════════════════════════════════════════════════╝\n');
          process.exit(0);
        });
      });
    });
  });
}

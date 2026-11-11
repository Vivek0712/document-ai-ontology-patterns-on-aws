/**
 * Writes the rp-ingest ASL with placeholder ARNs to contracts/generated/ingest_asl.json, the input of the
 * Python ASL-interpreter integration test (tests/integration/test_asl_handlers.py). A jest test fails when
 * the checked-in file drifts from lib/ingest-asl.ts: rerun `npm run export-asl`.
 */
import * as fs from 'fs';
import * as path from 'path';
import { placeholderAsl } from '../lib/ingest-asl';

const out = path.resolve(__dirname, '..', '..', 'contracts', 'generated', 'ingest_asl.json');
fs.writeFileSync(out, JSON.stringify(placeholderAsl(), null, 2) + '\n');
console.log(`wrote ${out}`);

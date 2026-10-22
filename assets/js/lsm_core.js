/**
 * LSM-Tree Key-Value Engine In-Browser Core
 * Mirrors the exact architecture of lsm/ (WAL, SkipList, BloomFilter, SSTables, Compactor)
 */

// 1. IEEE 802.3 CRC32 Implementation matching Python zlib.crc32
const CRC32_TABLE = new Uint32Array(256);
(function initCRC32() {
    for (let i = 0; i < 256; i++) {
        let c = i;
        for (let j = 0; j < 8; j++) {
            c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
        }
        CRC32_TABLE[i] = c >>> 0;
    }
})();

function computeCRC32(strOrBytes) {
    let crc = 0xFFFFFFFF;
    const bytes = typeof strOrBytes === 'string' ? new TextEncoder().encode(strOrBytes) : strOrBytes;
    for (let i = 0; i < bytes.length; i++) {
        crc = (crc >>> 8) ^ CRC32_TABLE[(crc ^ bytes[i]) & 0xFF];
    }
    return ((crc ^ 0xFFFFFFFF) >>> 0);
}

// Simple FNV-1a & Murmur-style 32-bit hash pair for Bloom Filter double-hashing
function hashPair(key) {
    let h1 = 0x811c9dc5;
    for (let i = 0; i < key.length; i++) {
        h1 ^= key.charCodeAt(i);
        h1 = Math.imul(h1, 0x01000193) >>> 0;
    }
    let h2 = 0x27d4eb2f;
    for (let i = key.length - 1; i >= 0; i--) {
        h2 ^= key.charCodeAt(i);
        h2 = Math.imul(h2, 0x1000193) >>> 0;
    }
    if (h2 === 0) h2 = 1;
    return [h1 >>> 0, h2 >>> 0];
}

// 2. Space-efficient Kirsch-Mitzenmacher Bloom Filter
class SimBloomFilter {
    constructor(numBits = 64, numHashes = 3) {
        this.numBits = numBits;
        this.numHashes = numHashes;
        this.bits = new Array(numBits).fill(0);
        this.addedKeys = [];
    }

    add(key) {
        const [h1, h2] = hashPair(key);
        const bitIndices = [];
        for (let i = 0; i < this.numHashes; i++) {
            const bitIndex = (h1 + i * h2) % this.numBits;
            this.bits[bitIndex] = 1;
            bitIndices.push(bitIndex);
        }
        this.addedKeys.push({ key, bitIndices });
        return bitIndices;
    }

    check(key) {
        const [h1, h2] = hashPair(key);
        const bitIndices = [];
        for (let i = 0; i < this.numHashes; i++) {
            const bitIndex = (h1 + i * h2) % this.numBits;
            bitIndices.push(bitIndex);
            if (this.bits[bitIndex] === 0) {
                return { hit: false, failedIndex: bitIndex, bitIndices };
            }
        }
        return { hit: true, bitIndices };
    }

    clone() {
        const bf = new SimBloomFilter(this.numBits, this.numHashes);
        bf.bits = [...this.bits];
        bf.addedKeys = [...this.addedKeys];
        return bf;
    }
}

// 3. SkipList Probabilistic Ordered Map
class SkipListNode {
    constructor(key, value, timestampNs, isTombstone, level, lsn = 0) {
        this.key = key;
        this.value = value;
        this.timestampNs = timestampNs;
        this.isTombstone = isTombstone;
        this.lsn = lsn;
        this.forward = new Array(level).fill(null);
    }
}

class SimSkipList {
    constructor(maxLevel = 5, p = 0.5) {
        this.maxLevel = maxLevel;
        this.p = p;
        this.level = 1;
        this.head = new SkipListNode("", null, 0, false, maxLevel);
        this.count = 0;
    }

    randomLevel() {
        let lvl = 1;
        while (Math.random() < this.p && lvl < this.maxLevel) {
            lvl++;
        }
        return lvl;
    }

    insert(key, value, timestampNs, isTombstone = false, lsn = 0) {
        const update = new Array(this.maxLevel).fill(null);
        let current = this.head;

        for (let i = this.level - 1; i >= 0; i--) {
            while (current.forward[i] && current.forward[i].key < key) {
                current = current.forward[i];
            }
            update[i] = current;
        }

        current = current.forward[0];

        if (current && current.key === key) {
            // Update existing node
            current.value = value;
            current.timestampNs = timestampNs;
            current.isTombstone = isTombstone;
            current.lsn = lsn;
            return { updated: true, level: current.forward.length };
        } else {
            const rLevel = this.randomLevel();
            if (rLevel > this.level) {
                for (let i = this.level; i < rLevel; i++) {
                    update[i] = this.head;
                }
                this.level = rLevel;
            }

            const newNode = new SkipListNode(key, value, timestampNs, isTombstone, rLevel, lsn);
            for (let i = 0; i < rLevel; i++) {
                newNode.forward[i] = update[i].forward[i];
                update[i].forward[i] = newNode;
            }
            this.count++;
            return { updated: false, level: rLevel };
        }
    }

    search(key) {
        let current = this.head;
        const searchPath = [];
        for (let i = this.level - 1; i >= 0; i--) {
            while (current.forward[i] && current.forward[i].key < key) {
                searchPath.push({ level: i, key: current.forward[i].key, step: "forward" });
                current = current.forward[i];
            }
            searchPath.push({ level: i, key: current.key || "HEAD", step: "drop" });
        }
        current = current.forward[0];
        if (current && current.key === key) {
            return { found: true, node: current, searchPath };
        }
        return { found: false, searchPath };
    }

    getAllSorted() {
        const records = [];
        let curr = this.head.forward[0];
        while (curr) {
            records.push({
                key: curr.key,
                value: curr.value,
                timestampNs: curr.timestampNs,
                isTombstone: curr.isTombstone,
                lsn: curr.lsn
            });
            curr = curr.forward[0];
        }
        return records;
    }

    getTowerStructure() {
        const nodes = [];
        let curr = this.head.forward[0];
        while (curr) {
            nodes.push({
                key: curr.key,
                value: curr.value,
                isTombstone: curr.isTombstone,
                height: curr.forward.length,
                lsn: curr.lsn
            });
            curr = curr.forward[0];
        }
        return nodes;
    }
}

// 4. SSTable Structure
class SimSSTable {
    constructor(id, records, blockSize = 2) {
        this.id = id;
        this.createdAt = new Date().toISOString().split("T")[1].slice(0, 8);
        this.records = records;
        this.recordCount = records.length;
        this.blockSize = blockSize;
        this.bloomFilter = new SimBloomFilter(64, 3);
        this.sparseIndex = [];
        this.dataBlocks = [];

        this._buildTable();
    }

    _buildTable() {
        let currentBlock = [];
        let currentOffset = 0;

        for (let i = 0; i < this.records.length; i++) {
            const rec = this.records[i];
            this.bloomFilter.add(rec.key);
            currentBlock.push(rec);

            if (currentBlock.length === this.blockSize || i === this.records.length - 1) {
                const firstKey = currentBlock[0].key;
                const size = currentBlock.length * 48; // simulated byte length
                this.sparseIndex.push({
                    key: firstKey,
                    offset: currentOffset,
                    size: size
                });
                this.dataBlocks.push({
                    blockIndex: this.dataBlocks.length,
                    offset: currentOffset,
                    firstKey: firstKey,
                    records: [...currentBlock]
                });
                currentOffset += size;
                currentBlock = [];
            }
        }
        this.totalSizeBytes = currentOffset + 128; // Data blocks + footer
    }

    search(key) {
        // Step 1: Bloom filter check
        const bfResult = this.bloomFilter.check(key);
        if (!bfResult.hit) {
            return {
                found: false,
                bloomHit: false,
                bitIndices: bfResult.bitIndices,
                failedIndex: bfResult.failedIndex,
                steps: [`Bloom filter rejected '${key}' (bit ${bfResult.failedIndex} == 0). Zero disk I/O!`]
            };
        }

        // Step 2: Sparse index binary search to find target data block
        const steps = [
            `Bloom filter passed (bits: [${bfResult.bitIndices.join(", ")}]). Proceeding to Sparse Index.`
        ];

        let targetBlockIdx = 0;
        for (let i = this.sparseIndex.length - 1; i >= 0; i--) {
            if (key >= this.sparseIndex[i].key) {
                targetBlockIdx = i;
                break;
            }
        }

        steps.push(`Sparse Index binary search: closest lower-bound index entry is Block #${targetBlockIdx} (Key >= '${this.sparseIndex[targetBlockIdx]?.key || ""}').`);

        // Step 3: Scan data block
        const block = this.dataBlocks[targetBlockIdx];
        if (!block) {
            return { found: false, bloomHit: true, steps };
        }

        steps.push(`Loaded Data Block #${targetBlockIdx} (Offset ${block.offset}). Scanning ${block.records.length} records.`);

        for (const rec of block.records) {
            if (rec.key === key) {
                if (rec.isTombstone) {
                    steps.push(`Found tombstone 🪦 for '${key}' at LSN #${rec.lsn}. Key was deleted.`);
                    return { found: true, isTombstone: true, record: rec, bloomHit: true, steps };
                } else {
                    steps.push(`Exact match found! Value: "${rec.value}" (LSN #${rec.lsn}).`);
                    return { found: true, isTombstone: false, record: rec, bloomHit: true, steps };
                }
            }
        }

        steps.push(`Key '${key}' not in block (False Positive Bloom Filter hit or non-existent in this SSTable).`);
        return { found: false, bloomHit: true, steps };
    }
}

// 5. LSM-Tree Simulation Coordinator
class SimLSMTree {
    constructor(maxMemTableKeys = 4) {
        this.maxMemTableKeys = maxMemTableKeys;
        this.lsnCounter = 0;
        this.memTable = new SimSkipList();
        this.wal = [];
        this.sstables = [];
        this.sstableSeq = 1;
        this.systemCrashed = false;
        this.tornFrameInjected = false;
        this.ioStats = {
            totalPuts: 0,
            totalGets: 0,
            bloomFilteredSkips: 0,
            diskBlockReads: 0,
            fsyncOps: 0
        };
    }

    put(key, value) {
        if (this.systemCrashed) throw new Error("Database crashed! Run recovery first.");
        this.lsnCounter++;
        const lsn = this.lsnCounter;
        const nowNs = Date.now() * 1000000;
        const payloadStr = `PUT:${lsn}:${key}:${value}`;
        const crc32 = computeCRC32(payloadStr);

        // 1. Write to WAL + fsync
        const walEntry = {
            lsn,
            type: "PUT",
            key,
            value,
            crc32: "0x" + crc32.toString(16).toUpperCase().padStart(8, "0"),
            rawCrc: crc32,
            length: payloadStr.length + 25,
            timestamp: new Date().toLocaleTimeString(),
            status: "FSYNCED",
            corrupted: false
        };
        this.wal.push(walEntry);
        this.ioStats.fsyncOps++;
        this.ioStats.totalPuts++;

        // 2. Insert into MemTable SkipList
        this.memTable.insert(key, value, nowNs, false, lsn);

        // 3. Auto-flush if capacity reached
        let flushed = false;
        if (this.memTable.count >= this.maxMemTableKeys) {
            this.flushMemTable();
            flushed = true;
        }

        return { lsn, walEntry, flushed };
    }

    delete(key) {
        if (this.systemCrashed) throw new Error("Database crashed! Run recovery first.");
        this.lsnCounter++;
        const lsn = this.lsnCounter;
        const nowNs = Date.now() * 1000000;
        const payloadStr = `DEL:${lsn}:${key}:TOMBSTONE`;
        const crc32 = computeCRC32(payloadStr);

        const walEntry = {
            lsn,
            type: "DELETE",
            key,
            value: null,
            crc32: "0x" + crc32.toString(16).toUpperCase().padStart(8, "0"),
            rawCrc: crc32,
            length: payloadStr.length + 25,
            timestamp: new Date().toLocaleTimeString(),
            status: "FSYNCED",
            corrupted: false
        };
        this.wal.push(walEntry);
        this.ioStats.fsyncOps++;

        this.memTable.insert(key, null, nowNs, true, lsn);

        let flushed = false;
        if (this.memTable.count >= this.maxMemTableKeys) {
            this.flushMemTable();
            flushed = true;
        }

        return { lsn, walEntry, flushed };
    }

    get(key) {
        if (this.systemCrashed) throw new Error("Database crashed! Run recovery first.");
        this.ioStats.totalGets++;
        const trace = {
            key,
            steps: [],
            found: false,
            value: null,
            isTombstone: false,
            location: null
        };

        // Step 1: Check Active MemTable
        trace.steps.push({
            stage: "Active MemTable (SkipList)",
            desc: `Searching active in-memory SkipList for '${key}'...`
        });
        const memResult = this.memTable.search(key);
        if (memResult.found) {
            if (memResult.node.isTombstone) {
                trace.found = true;
                trace.isTombstone = true;
                trace.location = "MemTable (Tombstone)";
                trace.steps.push({
                    stage: "MemTable Hit",
                    desc: `Key found as Tombstone 🪦 in Active MemTable (LSN #${memResult.node.lsn}). Return NOT FOUND (Key deleted).`
                });
                return trace;
            } else {
                trace.found = true;
                trace.value = memResult.node.value;
                trace.location = "MemTable";
                trace.steps.push({
                    stage: "MemTable Hit",
                    desc: `Found in Active MemTable! Value: "${memResult.node.value}" (LSN #${memResult.node.lsn}). 0 disk I/O required.`
                });
                return trace;
            }
        }
        trace.steps.push({
            stage: "MemTable Miss",
            desc: `Key '${key}' not in Active MemTable. Checking ${this.sstables.length} SSTables (newest to oldest)...`
        });

        // Step 2: Check SSTables in reverse chronological order
        for (let i = this.sstables.length - 1; i >= 0; i--) {
            const sst = this.sstables[i];
            trace.steps.push({
                stage: `Checking ${sst.id}`,
                desc: `Evaluating Bloom Filter for ${sst.id}...`
            });

            const sstResult = sst.search(key);
            if (!sstResult.bloomHit) {
                this.ioStats.bloomFilteredSkips++;
                trace.steps.push({
                    stage: `${sst.id} Bloom Filter Skip`,
                    desc: `⚡ Bloom filter returned FALSE. Zero disk I/O! Completely skipped ${sst.id}.`
                });
                continue;
            }

            this.ioStats.diskBlockReads++;
            for (const stepMsg of sstResult.steps) {
                trace.steps.push({
                    stage: `${sst.id} Disk Read`,
                    desc: stepMsg
                });
            }

            if (sstResult.found) {
                if (sstResult.isTombstone) {
                    trace.found = true;
                    trace.isTombstone = true;
                    trace.location = `${sst.id} (Tombstone)`;
                    return trace;
                } else {
                    trace.found = true;
                    trace.value = sstResult.record.value;
                    trace.location = sst.id;
                    return trace;
                }
            }
        }

        trace.steps.push({
            stage: "Final Result",
            desc: `Key '${key}' does not exist in any MemTable or SSTable.`
        });
        return trace;
    }

    scan(startKey, endKey) {
        if (this.systemCrashed) throw new Error("Database crashed! Run recovery first.");
        // Collect all records from MemTable + all SSTables, newest wins
        const map = new Map();

        // 1. Oldest SSTables to newest
        for (const sst of this.sstables) {
            for (const block of sst.dataBlocks) {
                for (const rec of block.records) {
                    if ((!startKey || rec.key >= startKey) && (!endKey || rec.key <= endKey)) {
                        map.set(rec.key, rec);
                    }
                }
            }
        }

        // 2. Active MemTable (latest overrides)
        const memRecords = this.memTable.getAllSorted();
        for (const rec of memRecords) {
            if ((!startKey || rec.key >= startKey) && (!endKey || rec.key <= endKey)) {
                map.set(rec.key, rec);
            }
        }

        // Filter out tombstones and sort by key
        const results = [];
        const sortedKeys = Array.from(map.keys()).sort();
        for (const k of sortedKeys) {
            const rec = map.get(k);
            if (!rec.isTombstone) {
                results.push({ key: rec.key, value: rec.value, lsn: rec.lsn });
            }
        }
        return results;
    }

    flushMemTable() {
        const records = this.memTable.getAllSorted();
        if (records.length === 0) return null;

        const sstId = `sst_${String(this.sstableSeq).padStart(3, "0")}.sst`;
        this.sstableSeq++;

        const sstable = new SimSSTable(sstId, records);
        this.sstables.push(sstable);

        // Reset MemTable
        this.memTable = new SimSkipList();
        return sstable;
    }

    compact() {
        if (this.sstables.length < 2) return null;

        // K-Way Merge: Gather all records across all SSTables, newer versions supersede older ones
        const map = new Map();
        for (const sst of this.sstables) {
            for (const block of sst.dataBlocks) {
                for (const rec of block.records) {
                    // map.set replaces older versions since sstables are traversed oldest to newest
                    map.set(rec.key, rec);
                }
            }
        }

        // At bottommost level (which we simulate here), eliminate tombstones
        const compactedRecords = [];
        const sortedKeys = Array.from(map.keys()).sort();
        for (const k of sortedKeys) {
            const rec = map.get(k);
            if (!rec.isTombstone) {
                compactedRecords.push(rec);
            }
        }

        const newId = `sst_compacted_${String(this.sstableSeq++).padStart(3, "0")}.sst`;
        const newSST = new SimSSTable(newId, compactedRecords);

        const oldTableCount = this.sstables.length;
        this.sstables = [newSST];

        return {
            newTable: newSST,
            oldTableCount,
            purgedRecords: (map.size - compactedRecords.length)
        };
    }

    simulateTornWriteCrash() {
        this.systemCrashed = true;
        this.tornFrameInjected = true;
        const fakeLsn = this.lsnCounter + 1;
        // Inject a mutilated frame into WAL (torn byte frame with invalid CRC32)
        this.wal.push({
            lsn: fakeLsn,
            type: "PUT (TORN WRITE)",
            key: "user:crash_demo",
            value: "truncated_payload_cut_by_power_l...",
            crc32: "0xDEADBEEF (MISMATCH)",
            rawCrc: 0xDEADBEEF,
            length: 54,
            timestamp: new Date().toLocaleTimeString(),
            status: "TORN_CORRUPTED",
            corrupted: true
        });
        // Clear active MemTable to simulate volatile RAM erasure
        this.memTable = new SimSkipList();
        return fakeLsn;
    }

    recover() {
        if (!this.systemCrashed) return { recovered: 0, truncatedTornFrames: 0 };
        const validFrames = [];
        let truncatedCount = 0;

        for (const frame of this.wal) {
            if (frame.corrupted) {
                truncatedCount++;
                // Stop at torn write boundary (truncate file back to last valid frame)
                break;
            }
            validFrames.push(frame);
        }

        // Replay valid frames into MemTable
        this.memTable = new SimSkipList();
        this.wal = [...validFrames];
        for (const frame of validFrames) {
            const nowNs = Date.now() * 1000000;
            this.memTable.insert(frame.key, frame.value, nowNs, frame.type === "DELETE", frame.lsn);
        }

        this.systemCrashed = false;
        this.tornFrameInjected = false;
        return {
            recovered: validFrames.length,
            truncatedTornFrames: truncatedCount
        };
    }
}

window.SimLSMTree = SimLSMTree;
window.computeCRC32 = computeCRC32;
window.hashPair = hashPair;

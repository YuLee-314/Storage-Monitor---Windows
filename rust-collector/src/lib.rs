use pyo3::prelude::*;
use windows::core::PCWSTR;
use windows::Win32::Storage::FileSystem::{
    GetDiskFreeSpaceExW, GetDriveTypeW, GetLogicalDrives, GetVolumeInformationW,
};

// Windows drive type constants (raw values for compatibility across windows crate versions)
// DRIVE_UNKNOWN = 0, DRIVE_NO_ROOT_DIR = 1, DRIVE_REMOVABLE = 2
// DRIVE_FIXED = 3, DRIVE_REMOTE = 4, DRIVE_CDROM = 5, DRIVE_RAMDISK = 6
fn drive_type_name(dt: u32) -> &'static str {
    match dt {
        2 => "Removable",
        3 => "Fixed",
        4 => "Remote",
        5 => "CD-ROM",
        6 => "RAM Disk",
        _ => "Other",
    }
}

#[pyclass]
#[derive(Clone)]
struct DiskInfo {
    #[pyo3(get)]
    drive_letter: String,
    #[pyo3(get)]
    label: String,
    #[pyo3(get)]
    filesystem: String,
    #[pyo3(get)]
    drive_type: String,
    #[pyo3(get)]
    total_bytes: u64,
    #[pyo3(get)]
    used_bytes: u64,
    #[pyo3(get)]
    free_bytes: u64,
    #[pyo3(get)]
    usage_percent: f64,
}

fn query_drive(drive_letter: char) -> Option<DiskInfo> {
    let root_path: Vec<u16> = format!("{}:\\\0", drive_letter).encode_utf16().collect();
    let root_pcwstr = PCWSTR::from_raw(root_path.as_ptr());

    let drive_type = unsafe { GetDriveTypeW(root_pcwstr) };
    if drive_type == 0 || drive_type == 1 {
        return None;
    }

    // Get free/used/total space
    let mut free_bytes_available: u64 = 0;
    let mut total_bytes: u64 = 0;
    let mut total_free_bytes: u64 = 0;
    unsafe {
        if GetDiskFreeSpaceExW(
            root_pcwstr,
            Some(&mut free_bytes_available),
            Some(&mut total_bytes),
            Some(&mut total_free_bytes),
        )
        .is_err()
        {
            return None;
        }
    }

    // Get volume label and filesystem (output params mutated in place)
    let mut label_buf = [0u16; 128];
    let mut fs_buf = [0u16; 128];
    let _ = unsafe {
        GetVolumeInformationW(
            root_pcwstr,
            Some(&mut label_buf),
            None,
            None,
            None,
            Some(&mut fs_buf),
        )
    };

    let label = String::from_utf16_lossy(&label_buf)
        .trim_end_matches('\0')
        .to_string();
    let fs = String::from_utf16_lossy(&fs_buf)
        .trim_end_matches('\0')
        .to_string();

    let used_bytes = total_bytes.saturating_sub(free_bytes_available);
    let usage_percent = if total_bytes > 0 {
        (used_bytes as f64 / total_bytes as f64) * 100.0
    } else {
        0.0
    };

    Some(DiskInfo {
        drive_letter: format!("{}:", drive_letter),
        label,
        filesystem: fs,
        drive_type: drive_type_name(drive_type).to_string(),
        total_bytes,
        used_bytes,
        free_bytes: free_bytes_available,
        usage_percent: (usage_percent * 10.0).round() / 10.0,
    })
}

#[pyfunction]
fn get_all_disk_info() -> PyResult<Vec<DiskInfo>> {
    let drives = unsafe { GetLogicalDrives() };
    let mut result = Vec::new();

    for i in 0..26 {
        if (drives & (1 << i)) != 0 {
            let drive_letter = (b'A' + i) as char;
            if let Some(info) = query_drive(drive_letter) {
                result.push(info);
            }
        }
    }

    result.sort_by(|a, b| a.drive_letter.cmp(&b.drive_letter));
    Ok(result)
}

// ── File scanner ────────────────────────────────────────────────────

#[pyclass]
#[derive(Clone)]
struct FileEntry {
    #[pyo3(get)]
    path: String,
    #[pyo3(get)]
    name: String,
    #[pyo3(get)]
    size_bytes: u64,
    #[pyo3(get)]
    is_dir: bool,
}

#[pyfunction]
fn scan_largest_files(root: String, top_n: usize) -> PyResult<Vec<FileEntry>> {
    use std::collections::BinaryHeap;
    use std::cmp::Ordering;
    use std::collections::HashMap;

    #[derive(Eq, PartialEq)]
    struct SizedEntry {
        path: String,
        name: String,
        size: u64,
        is_dir: bool,
    }

    impl Ord for SizedEntry {
        fn cmp(&self, other: &Self) -> Ordering {
            self.size.cmp(&other.size)
        }
    }
    impl PartialOrd for SizedEntry {
        fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
            Some(self.cmp(other))
        }
    }

    // First pass: collect individual file sizes and build directory tree
    let mut file_sizes: Vec<(String, String, u64)> = Vec::new(); // (path, name, size)
    let mut dir_sizes: HashMap<String, u64> = HashMap::new();
    let mut dir_names: HashMap<String, String> = HashMap::new();

    for entry in walkdir::WalkDir::new(&root)
        .follow_links(false)
        .into_iter()
        .filter_map(|e| e.ok())
        .filter(|e| {
            // Skip protected system directories and their contents
            let mut p = e.path().parent();
            while let Some(parent) = p {
                if is_protected_path(parent) {
                    return false;
                }
                p = parent.parent();
            }
            !is_protected_path(e.path())
        })
    {
        let path = entry.path().to_string_lossy().to_string();
        let name = entry
            .file_name()
            .to_string_lossy()
            .to_string();

        if entry.file_type().is_file() {
            let size = entry.metadata().map(|m| m.len()).unwrap_or(0);
            file_sizes.push((path.clone(), name, size));

            // Propagate size up through parent directories
            let mut parent = entry.path().parent();
            while let Some(p) = parent {
                let parent_path = p.to_string_lossy().to_string();
                if parent_path.len() < root.len() {
                    break;
                }
                *dir_sizes.entry(parent_path.clone()).or_insert(0) += size;
                // Capture dir name
                if let Some(dir_name) = p.file_name() {
                    dir_names
                        .entry(parent_path.clone())
                        .or_insert_with(|| dir_name.to_string_lossy().to_string());
                }
                parent = p.parent();
            }
        }
    }

    // Use a min-heap to track top N items
    let mut heap: BinaryHeap<SizedEntry> = BinaryHeap::new();

    let mut push = |path: String, name: String, size: u64, is_dir: bool| {
        if size == 0 {
            return;
        }
        if heap.len() < top_n {
            heap.push(SizedEntry { path, name, size, is_dir });
        } else if let Some(smallest) = heap.peek() {
            if size > smallest.size {
                heap.pop();
                heap.push(SizedEntry { path, name, size, is_dir });
            }
        }
    };

    // Push individual files
    for (path, name, size) in &file_sizes {
        push(path.clone(), name.clone(), *size, false);
    }

    // Push directory aggregates (only top-level subdirs of root to avoid double-counting)
    for (dir_path, size) in &dir_sizes {
        // Only include direct children of root to avoid nesting confusion
        let relative = dir_path
            .strip_prefix(&root)
            .unwrap_or(dir_path)
            .trim_start_matches(&['\\', '/'][..]);
        // Count depth: only include first-level and second-level dirs
        let depth = relative.split(&['\\', '/'][..]).count();
        if depth <= 2 {
            if let Some(name) = dir_names.get(dir_path) {
                push(dir_path.clone(), name.clone(), *size, true);
            }
        }
    }

    // Drain heap into sorted vec (largest first)
    let mut result: Vec<SizedEntry> = heap.into_sorted_vec();
    result.reverse();

    Ok(result
        .into_iter()
        .map(|e| FileEntry {
            path: e.path,
            name: e.name,
            size_bytes: e.size,
            is_dir: e.is_dir,
        })
        .collect())
}

// ── Directory listing ───────────────────────────────────────────────

/// System directories known to block or hang when enumerated.
fn is_protected_dir(name: &str) -> bool {
    let lower = name.to_lowercase();
    lower == "system volume information"
        || lower == "$recycle.bin"
        || lower == "$winreagent"
        || lower == "config.msi"
        || lower == "documents and settings"  // junction
        || lower == "msocache"
        || lower.starts_with("$")
}

fn is_protected_file(name: &str) -> bool {
    let lower = name.to_lowercase();
    lower == "pagefile.sys"
        || lower == "swapfile.sys"
        || lower == "hiberfil.sys"
        || lower == "dumpstack.log.tmp"
}

/// Return immediate children of `path`.  Files report their own size;
/// directories report 0.  Protected system entries are skipped.
/// On Windows (NTFS), file_type() and metadata() are free — the info is
/// already in the directory entry (dust-style fast path, avoids Defender).
/// Results: directories first (alphabetical), then files largest-first.
#[pyfunction]
fn get_dir_contents(path: String) -> PyResult<Vec<FileEntry>> {
    let root = std::path::Path::new(&path);
    if !root.is_dir() {
        return Ok(Vec::new());
    }

    let mut dirs: Vec<FileEntry> = Vec::new();
    let mut files: Vec<FileEntry> = Vec::new();

    if let Ok(iter) = std::fs::read_dir(root) {
        for entry in iter.flatten() {
            let child_path = entry.path();
            let name = child_path
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default();

            // Fast path: file_type() is free on NTFS (from dir entry)
            let ft = match entry.file_type() {
                Ok(ft) => ft,
                Err(_) => continue,
            };

            if ft.is_dir() {
                if is_protected_dir(&name) { continue; }
            } else if ft.is_file() {
                if is_protected_file(&name) { continue; }
            } else {
                continue; // reparse points, junctions, etc.
            }

            let is_dir = ft.is_dir();
            // metadata() is free for normal NTFS files (already in dir entry)
            let size: u64 = if is_dir {
                0
            } else {
                entry.metadata().map(|m| m.len()).unwrap_or(0)
            };

            let fe = FileEntry {
                path: child_path.to_string_lossy().to_string(),
                name,
                size_bytes: size,
                is_dir,
            };

            if is_dir { dirs.push(fe); } else { files.push(fe); }
        }
    }

    dirs.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    files.sort_by(|a, b| b.size_bytes.cmp(&a.size_bytes));
    dirs.append(&mut files);
    Ok(dirs)
}

/// Same skip logic for the full-tree scanner.
fn is_protected_path(path: &std::path::Path) -> bool {
    path.file_name()
        .map(|n| {
            let name = n.to_string_lossy();
            is_protected_dir(&name) || is_protected_file(&name)
        })
        .unwrap_or(false)
}

/// Like get_dir_contents but computes recursive sizes for directories.
/// Accepts an optional progress callback: `callback(path_str)` called
/// when computing the size of each subdirectory.
#[pyfunction]
#[pyo3(signature = (path, progress=None))]
fn get_dir_contents_sized(
    path: String,
    progress: Option<PyObject>,
    py: Python<'_>,
) -> PyResult<Vec<FileEntry>> {
    let root = std::path::Path::new(&path);
    if !root.is_dir() {
        return Ok(Vec::new());
    }

    let mut dirs: Vec<FileEntry> = Vec::new();
    let mut files: Vec<FileEntry> = Vec::new();

    if let Ok(iter) = std::fs::read_dir(root) {
        for entry in iter.flatten() {
            let child_path = entry.path();
            let name = child_path
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default();

            // Windows fast path: file_type() + metadata() are free from dir entry
            let ft = match entry.file_type() {
                Ok(ft) => ft,
                Err(_) => continue,
            };

            if ft.is_dir() {
                if is_protected_dir(&name) { continue; }
            } else if ft.is_file() {
                if is_protected_file(&name) { continue; }
            } else {
                continue;
            }

            let is_dir = ft.is_dir();
            let size: u64 = if is_dir {
                // Signal progress
                if let Some(ref cb) = progress {
                    let _ = cb.call1(py, (child_path.to_string_lossy().to_string(),));
                }
                // Recursive walk with protected-dir skipping
                walkdir::WalkDir::new(&child_path)
                    .follow_links(false)
                    .into_iter()
                    .filter_map(|e| e.ok())
                    .filter(|e| e.file_type().is_file())
                    .filter(|e| !is_protected_path(e.path()))
                    .filter(|e| {
                        let mut p = e.path().parent();
                        while let Some(parent) = p {
                            if is_protected_path(parent) { return false; }
                            p = parent.parent();
                        }
                        true
                    })
                    .map(|e| e.metadata().map(|m| m.len()).unwrap_or(0))
                    .sum()
            } else {
                entry.metadata().map(|m| m.len()).unwrap_or(0)
            };

            let fe = FileEntry {
                path: child_path.to_string_lossy().to_string(),
                name,
                size_bytes: size,
                is_dir,
            };

            if is_dir { dirs.push(fe); } else { files.push(fe); }
        }
    }

    dirs.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    files.sort_by(|a, b| b.size_bytes.cmp(&a.size_bytes));
    dirs.append(&mut files);
    Ok(dirs)
}

// ── Single-pass tree size ──────────────────────────────────────────

/// Walk `path` once, accumulating directory sizes bottom-up (like dust/rdirstat).
/// Returns sizes for immediate children only.  Much faster than calling
/// `get_dir_contents_sized` because the tree is traversed only once.
#[pyfunction]
#[pyo3(signature = (path, progress=None))]
fn get_dir_tree_sizes(
    path: String,
    progress: Option<PyObject>,
    py: Python<'_>,
) -> PyResult<Vec<FileEntry>> {
    use std::collections::HashMap;

    let root = std::path::Path::new(&path);
    if !root.is_dir() {
        return Ok(Vec::new());
    }
    let root_str = root.to_string_lossy().to_string();

    let root_clone = root_str.clone();
    let (dir_sizes, _file_count) = py.allow_threads(move || -> (HashMap<String, u64>, u64) {
        let mut dir_sizes: HashMap<String, u64> = HashMap::new();
        let mut file_count: u64 = 0;

        for entry in walkdir::WalkDir::new(root)
            .follow_links(false)
            .into_iter()
            .filter_map(|e| e.ok())
        {
            if !entry.file_type().is_file() { continue; }
            let epath = entry.path();
            if is_protected_path(epath) { continue; }

            let size = entry.metadata().map(|m| m.len()).unwrap_or(0);
            if size == 0 { continue; }

            let mut parent = epath.parent();
            while let Some(p) = parent {
                let pstr = p.to_string_lossy().to_string();
                if pstr.len() < root_clone.len() { break; }
                if is_protected_path(p) { break; }
                *dir_sizes.entry(pstr.clone()).or_insert(0) += size;
                parent = p.parent();
            }
            file_count += 1;
        }
        (dir_sizes, file_count)
    });

    if let Some(ref cb) = progress {
        let _ = cb.call1(py, (format!("{} files scanned", _file_count),));
    }

    let mut result: Vec<FileEntry> = Vec::new();
    for (dir_path, size) in &dir_sizes {
        let rel = if let Some(stripped) = dir_path.strip_prefix(&root_str) {
            stripped.trim_start_matches(&['\\', '/'][..]).to_string()
        } else { continue; };
        if rel.is_empty() || rel.contains('\\') || rel.contains('/') { continue; }
        result.push(FileEntry {
            path: dir_path.clone(),
            name: rel,
            size_bytes: *size,
            is_dir: true,
        });
    }
    result.sort_by(|a, b| b.size_bytes.cmp(&a.size_bytes));
    Ok(result)
}

#[pymodule]
fn disk_collector(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(get_all_disk_info, m)?)?;
    m.add_function(wrap_pyfunction!(scan_largest_files, m)?)?;
    m.add_function(wrap_pyfunction!(get_dir_contents, m)?)?;
    m.add_function(wrap_pyfunction!(get_dir_contents_sized, m)?)?;
    m.add_function(wrap_pyfunction!(get_dir_tree_sizes, m)?)?;
    m.add_class::<DiskInfo>()?;
    m.add_class::<FileEntry>()?;
    Ok(())
}

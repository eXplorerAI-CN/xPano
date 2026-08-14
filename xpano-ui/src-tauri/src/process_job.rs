#[cfg(windows)]
mod platform {
    use std::io;
    use std::mem::{size_of, zeroed};
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    use windows_sys::Win32::System::Threading::{
        OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE,
    };

    pub struct ProcessJob {
        handle: HANDLE,
    }

    unsafe impl Send for ProcessJob {}

    impl ProcessJob {
        pub fn new() -> Result<Self, String> {
            let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
            if handle.is_null() {
                return Err(format!(
                    "创建 Windows Job Object 失败: {}",
                    io::Error::last_os_error()
                ));
            }

            let job = Self { handle };
            job.enable_kill_on_close()?;
            Ok(job)
        }

        pub fn assign_pid(&self, pid: u32) -> Result<(), String> {
            let process = unsafe { OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, pid) };
            if process.is_null() {
                return Err(format!(
                    "打开进程 {} 失败: {}",
                    pid,
                    io::Error::last_os_error()
                ));
            }

            let assigned = unsafe { AssignProcessToJobObject(self.handle, process) != 0 };
            unsafe {
                CloseHandle(process);
            }

            if assigned {
                Ok(())
            } else {
                Err(format!(
                    "进程 {} 加入 Windows Job Object 失败: {}",
                    pid,
                    io::Error::last_os_error()
                ))
            }
        }

        pub fn terminate(&self) {
            unsafe {
                TerminateJobObject(self.handle, 1);
            }
        }

        fn enable_kill_on_close(&self) -> Result<(), String> {
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;

            let ok = unsafe {
                SetInformationJobObject(
                    self.handle,
                    JobObjectExtendedLimitInformation,
                    &limits as *const _ as *const _,
                    size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                ) != 0
            };

            if ok {
                Ok(())
            } else {
                Err(format!(
                    "设置 Windows Job Object 关闭清理失败: {}",
                    io::Error::last_os_error()
                ))
            }
        }
    }

    impl Drop for ProcessJob {
        fn drop(&mut self) {
            if !self.handle.is_null() {
                unsafe {
                    CloseHandle(self.handle);
                }
            }
        }
    }
}

#[cfg(not(windows))]
mod platform {
    pub struct ProcessJob;

    impl ProcessJob {
        pub fn new() -> Result<Self, String> {
            Ok(Self)
        }

        pub fn assign_pid(&self, _pid: u32) -> Result<(), String> {
            Ok(())
        }

        pub fn terminate(&self) {}
    }
}

pub use platform::ProcessJob;

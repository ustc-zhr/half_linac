TOP=../..

include $(TOP)/configure/CONFIG
#----------------------------------------
#  ADD MACRO DEFINITIONS BELOW HERE

# Use typed rset structure (see 3.16.1 release notes)
USR_CPPFLAGS += -DUSE_TYPED_RSET

# xxxRecord.h will be created from xxxRecord.dbd
DBDINC += xxxRecord

# Install xxxSupport.dbd into <top>/dbd
DBD += xxxSupport.dbd

# Build an IOC support library
LIBRARY_IOC += targetSupport

# Compile and add code to the support library
targetSupport_SRCS += xxxRecord.c
targetSupport_SRCS += devXxxSoft.c

# Link locally-provided code into the support library,
# rather than directly into the IOC application, that
# causes problems on Windows DLL builds
targetSupport_SRCS += dbSubExample.c
targetSupport_SRCS += devtargetVersion.c
targetSupport_SRCS += targetHello.c
targetSupport_SRCS += initTrace.c

targetSupport_LIBS += $(EPICS_BASE_IOC_LIBS)

# Auto-generate a header file containing a version string.
# Version comes from the VCS if available, else date+time.
GENVERSION = targetVersion.h
# Macro name
GENVERSIONMACRO = targetVERSION

# Build the IOC application
PROD_IOC = target

# target.dbd will be created and installed
DBD += target.dbd

# target.dbd will include these files:
target_DBD += base.dbd
target_DBD += xxxSupport.dbd
target_DBD += dbSubExample.dbd
target_DBD += devtargetVersion.dbd
target_DBD += targetHello.dbd
target_DBD += initTrace.dbd

# target_registerRecordDeviceDriver.cpp derives from target.dbd
target_SRCS += target_registerRecordDeviceDriver.cpp

# Build the main IOC entry point where needed
target_SRCS_DEFAULT += targetMain.cpp
target_SRCS_vxWorks += -nil-

# Link in the code from our support library
target_LIBS += targetSupport

# To build SNL programs, SNCSEQ must be defined
# in the <top>/configure/RELEASE file
ifneq ($(SNCSEQ),)
    # Build sncExample into targetSupport
    sncExample_SNCFLAGS += +r
    target_DBD += sncExample.dbd
    # A .stt sequence program is *not* pre-processed:
    targetSupport_SRCS += sncExample.stt
    targetSupport_LIBS += seq pv
    target_LIBS += seq pv

    # Build sncProgram as a standalone program
    PROD_HOST += sncProgram
    sncProgram_SNCFLAGS += +m
    # A .st sequence program *is* pre-processed:
    sncProgram_SRCS += sncProgram.st
    sncProgram_LIBS += seq pv
    sncProgram_LIBS += $(EPICS_BASE_HOST_LIBS)
endif

# Link QSRV (pvAccess Server) if available
ifdef EPICS_QSRV_MAJOR_VERSION
    target_LIBS += qsrv
    target_LIBS += $(EPICS_BASE_PVA_CORE_LIBS)
    target_DBD += PVAServerRegister.dbd
    target_DBD += qsrv.dbd
endif

# Finally link IOC to the EPICS Base libraries
target_LIBS += $(EPICS_BASE_IOC_LIBS)

include $(TOP)/configure/RULES
#----------------------------------------
#  ADD EXTRA GNUMAKE RULES BELOW HERE

# Explicit dependency needed for generated header file
devtargetVersion$(DEP): $(COMMON_DIR)/$(GENVERSION)

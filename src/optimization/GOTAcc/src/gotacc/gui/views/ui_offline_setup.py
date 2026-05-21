from PyQt5 import QtCore, QtWidgets


class Ui_OfflineSetupPage(object):
    def setupUi(self, OfflineSetupPage):
        OfflineSetupPage.setObjectName("OfflineSetupPage")
        OfflineSetupPage.resize(1280, 920)
        self.verticalLayout_main = QtWidgets.QVBoxLayout(OfflineSetupPage)
        self.verticalLayout_main.setObjectName("verticalLayout_main")
        self.frame_offlineHero = QtWidgets.QFrame(OfflineSetupPage)
        self.frame_offlineHero.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.frame_offlineHero.setObjectName("frame_offlineHero")
        self.verticalLayout_offlineHero = QtWidgets.QVBoxLayout(self.frame_offlineHero)
        self.verticalLayout_offlineHero.setObjectName("verticalLayout_offlineHero")
        self.label_offlineTitle = QtWidgets.QLabel(self.frame_offlineHero)
        self.label_offlineTitle.setObjectName("label_offlineTitle")
        self.verticalLayout_offlineHero.addWidget(self.label_offlineTitle)
        self.label_offlineSummary = QtWidgets.QLabel(self.frame_offlineHero)
        self.label_offlineSummary.setWordWrap(True)
        self.label_offlineSummary.setObjectName("label_offlineSummary")
        self.verticalLayout_offlineHero.addWidget(self.label_offlineSummary)
        self.verticalLayout_main.addWidget(self.frame_offlineHero)
        self.groupBox_benchmark = QtWidgets.QGroupBox(OfflineSetupPage)
        self.groupBox_benchmark.setObjectName("groupBox_benchmark")
        self.verticalLayout_benchmark = QtWidgets.QVBoxLayout(self.groupBox_benchmark)
        self.verticalLayout_benchmark.setObjectName("verticalLayout_benchmark")
        self.formLayout_offlineConfig = QtWidgets.QFormLayout()
        self.formLayout_offlineConfig.setObjectName("formLayout_offlineConfig")
        self.verticalLayout_benchmark.addLayout(self.formLayout_offlineConfig)
        self.label_offlineHint = QtWidgets.QLabel(self.groupBox_benchmark)
        self.label_offlineHint.setWordWrap(True)
        self.label_offlineHint.setObjectName("label_offlineHint")
        self.verticalLayout_benchmark.addWidget(self.label_offlineHint)
        self.verticalLayout_main.addWidget(self.groupBox_benchmark)
        self.frame_offlinePlaceholder = QtWidgets.QFrame(OfflineSetupPage)
        self.frame_offlinePlaceholder.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.frame_offlinePlaceholder.setObjectName("frame_offlinePlaceholder")
        self.verticalLayout_placeholder = QtWidgets.QVBoxLayout(self.frame_offlinePlaceholder)
        self.verticalLayout_placeholder.setObjectName("verticalLayout_placeholder")
        self.label_offlinePlaceholder = QtWidgets.QLabel(self.frame_offlinePlaceholder)
        self.label_offlinePlaceholder.setWordWrap(True)
        self.label_offlinePlaceholder.setObjectName("label_offlinePlaceholder")
        self.verticalLayout_placeholder.addWidget(self.label_offlinePlaceholder)
        self.verticalLayout_main.addWidget(self.frame_offlinePlaceholder)
        self.verticalLayout_main.addStretch(1)

        self.retranslateUi(OfflineSetupPage)
        QtCore.QMetaObject.connectSlotsByName(OfflineSetupPage)

    def retranslateUi(self, OfflineSetupPage):
        _translate = QtCore.QCoreApplication.translate
        OfflineSetupPage.setWindowTitle(_translate("OfflineSetupPage", "Offline Setup"))
        self.label_offlineTitle.setText(_translate("OfflineSetupPage", "Offline Setup"))
        self.label_offlineSummary.setText(
            _translate(
                "OfflineSetupPage",
                "Configure benchmark-function inputs for offline validation and local smoke tests.",
            )
        )
        self.groupBox_benchmark.setTitle(_translate("OfflineSetupPage", "Benchmark Source"))
        self.label_offlineHint.setText(
            _translate(
                "OfflineSetupPage",
                "Choose the built-in benchmark function used when Mode is set to Offline. "
                "The tradeoff function is intended for multi-objective offline tasks.",
            )
        )
        self.label_offlinePlaceholder.setText(
            _translate(
                "OfflineSetupPage",
                "This page is reserved for additional offline-only setup controls.",
            )
        )

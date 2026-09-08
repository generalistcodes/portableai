import SwiftUI

struct PairingView: View {
    @EnvironmentObject var appState: AppState
    @State private var serverURL = "http://192.168.1."
    @State private var pin = ""
    @State private var deviceName = UIDevice.current.name

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("On your laptop, open PortableAI's Settings and look under \"Phone pairing\" for the address and a 6-digit PIN.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("Server address") {
                    TextField("http://192.168.1.42:5050", text: $serverURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }

                Section("Pairing PIN") {
                    TextField("123456", text: $pin)
                        .keyboardType(.numberPad)
                }

                Section("This device") {
                    TextField("Device name", text: $deviceName)
                }

                if let error = appState.pairingError {
                    Section {
                        Text(error)
                            .foregroundStyle(.red)
                    }
                }

                Section {
                    Button {
                        Task {
                            await appState.pair(serverURL: serverURL, pin: pin, deviceName: deviceName)
                        }
                    } label: {
                        if appState.isPairing {
                            ProgressView()
                        } else {
                            Text("Pair")
                        }
                    }
                    .disabled(serverURL.isEmpty || pin.count != 6 || appState.isPairing)
                }
            }
            .navigationTitle("Pair with PortableAI")
        }
    }
}

import half_linac.runtime_config as st

def get_energy0(current, L=2.7271, ANGLE=0.4363323129985824):
    """
    Convert ESA magnet current to energy0 (the energy to arrive the center 0 of the ESA flag):
    
    Parameters:
    current : float
        ESA magnet current in Amperes.
    L : float
        Length of the magnetic field region in meters.
    ANGLE : float
        deflect angle of the reference particle
    
    Returns:
    energy0 : float
        Beam energy in electron Volts (eV).
    """

    rho = L / ANGLE  # bending radius in meters

    # current_to_B0
    B0 = 0.599792458e-3 * current  # Tesla, B0 = 0.599792458 * current [A] * 1e-3
    
    # B0_to_energy0
    energy0 = B0 * rho * st.c_light * 1.e-6 # kinetic_energy [MeV]

    return energy0
